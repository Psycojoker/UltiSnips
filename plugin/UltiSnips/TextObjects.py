#!/usr/bin/env python
# encoding: utf-8

import os
import re
import stat
import tempfile
import vim

from UltiSnips.Buffer import TextBuffer
from UltiSnips.Compatibility import CheapTotalOrdering
from UltiSnips.Compatibility import compatible_exec, as_unicode
from UltiSnips.Geometry import Span, Position
from UltiSnips.Lexer import tokenize, EscapeCharToken, VisualToken, \
    TransformationToken, TabStopToken, MirrorToken, PythonCodeToken, \
    VimLCodeToken, ShellCodeToken
from UltiSnips.Util import IndentUtil

__all__ = [ "Mirror", "Transformation", "SnippetInstance" ]

from debug import debug
def _do_print(obj, indent =""): # TODO: remote again
    debug("%s %r" % (indent, obj))

    for c in obj._childs:
        _do_print(c, indent + "    ")

###########################################################################
#                              Helper class                               #
###########################################################################
class _CleverReplace(object):
    """
    This class mimics TextMates replace syntax
    """
    _DOLLAR = re.compile(r"\$(\d+)", re.DOTALL)
    _SIMPLE_CASEFOLDINGS = re.compile(r"\\([ul].)", re.DOTALL)
    _LONG_CASEFOLDINGS = re.compile(r"\\([UL].*?)\\E", re.DOTALL)
    _CONDITIONAL = re.compile(r"\(\?(\d+):", re.DOTALL)

    _UNESCAPE = re.compile(r'\\[^ntrab]')
    _SCHARS_ESCPAE = re.compile(r'\\[ntrab]')

    def __init__(self, s):
        self._s = s

    def _scase_folding(self, m):
        if m.group(1)[0] == 'u':
            return m.group(1)[-1].upper()
        else:
            return m.group(1)[-1].lower()
    def _lcase_folding(self, m):
        if m.group(1)[0] == 'U':
            return m.group(1)[1:].upper()
        else:
            return m.group(1)[1:].lower()

    def _replace_conditional(self, match, v):
        def _find_closingbrace(v,start_pos):
            bracks_open = 1
            for idx, c in enumerate(v[start_pos:]):
                if c == '(':
                    if v[idx+start_pos-1] != '\\':
                        bracks_open += 1
                elif c == ')':
                    if v[idx+start_pos-1] != '\\':
                        bracks_open -= 1
                    if not bracks_open:
                        return start_pos+idx+1
        m = self._CONDITIONAL.search(v)

        def _part_conditional(v):
            bracks_open = 0
            args = []
            carg = ""
            for idx, c in enumerate(v):
                if c == '(':
                    if v[idx-1] != '\\':
                        bracks_open += 1
                elif c == ')':
                    if v[idx-1] != '\\':
                        bracks_open -= 1
                elif c == ':' and not bracks_open and not v[idx-1] == '\\':
                    args.append(carg)
                    carg = ""
                    continue
                carg += c
            args.append(carg)
            return args

        while m:
            start = m.start()
            end = _find_closingbrace(v,start+4)

            args = _part_conditional(v[start+4:end-1])

            rv = ""
            if match.group(int(m.group(1))):
                rv = self._unescape(self._replace_conditional(match,args[0]))
            elif len(args) > 1:
                rv = self._unescape(self._replace_conditional(match,args[1]))

            v = v[:start] + rv + v[end:]

            m = self._CONDITIONAL.search(v)
        return v

    def _unescape(self, v):
        return self._UNESCAPE.subn(lambda m: m.group(0)[-1], v)[0]
    def _schar_escape(self, v):
        return self._SCHARS_ESCPAE.subn(lambda m: eval(r"'\%s'" % m.group(0)[-1]), v)[0]

    def replace(self, match):
        start, end = match.span()

        tv = self._s

        # Replace all $? with capture groups
        tv = self._DOLLAR.subn(lambda m: match.group(int(m.group(1))), tv)[0]

        # Replace CaseFoldings
        tv = self._SIMPLE_CASEFOLDINGS.subn(self._scase_folding, tv)[0]
        tv = self._LONG_CASEFOLDINGS.subn(self._lcase_folding, tv)[0]
        tv = self._replace_conditional(match, tv)

        return self._unescape(self._schar_escape(tv))

class _TOParser(object):
    def __init__(self, parent_to, text, indent):
        self._indent = indent
        self._parent_to = parent_to
        self._text = text

    def parse(self, add_ts_zero = False):
        seen_ts = {}
        all_tokens = []

        self._do_parse(all_tokens, seen_ts)

        self._resolve_ambiguity(all_tokens, seen_ts)
        self._create_objects_with_links_to_tabs(all_tokens, seen_ts)

        if add_ts_zero and 0 not in seen_ts:
            mark = all_tokens[-1][1].end # Last token is always EndOfText
            m1 = Position(mark.line, mark.col)
            self._parent_to._add_tabstop(TabStop(self._parent_to, 0, mark, m1))

        self._replace_initital_texts(seen_ts)

    #####################
    # Private Functions #
    #####################
    def _resolve_ambiguity(self, all_tokens, seen_ts):
        for parent, token in all_tokens:
            if isinstance(token, MirrorToken):
                if token.no not in seen_ts:
                    ts = TabStop(parent, token)
                    seen_ts[token.no] = ts
                    parent._add_tabstop(ts)
                else:
                    Mirror(parent, seen_ts[token.no], token)

    def _create_objects_with_links_to_tabs(self, all_tokens, seen_ts):
        for parent, token in all_tokens:
            if isinstance(token, TransformationToken):
                if token.no not in seen_ts:
                    raise RuntimeError("Tabstop %i is not known but is used by a Transformation" % token.no)
                Transformation(parent, seen_ts[token.no], token)

    def _replace_initital_texts(self, seen_ts):
        def _place_initial_text(obj):
            debug("#### Initial Text: %r" % obj)
            obj.initial_replace()

            par = obj
            while par._parent: par = par._parent
            _do_print(par)

            for c in obj._childs: # TODO: private parts!
                _place_initial_text(c)

        _place_initial_text(self._parent_to)

        def _update_non_tabstops(obj): # TODO: Stupid function name
            obj._really_updateman()

            for c in obj._childs:
                _update_non_tabstops(c)

        _update_non_tabstops(self._parent_to)

    # TODO: offset is no longer used
    def _do_parse(self, all_tokens, seen_ts, offset = None):
        if offset is None:
            offset = self._parent_to._start
        tokens = list(tokenize(self._text, self._indent, offset))
        debug("tokens: %r" % (tokens))

        for token in tokens:
            all_tokens.append((self._parent_to, token))

            if isinstance(token, TabStopToken):
                ts = TabStop(self._parent_to, token)
                seen_ts[token.no] = ts
                self._parent_to._add_tabstop(ts)

                k = _TOParser(ts, token.initial_text, self._indent)
                k._do_parse(all_tokens, seen_ts)
            elif isinstance(token, EscapeCharToken):
                EscapedChar(self._parent_to, token)
            elif isinstance(token, VisualToken):
                Visual(self._parent_to, token)
            elif isinstance(token, ShellCodeToken):
                ShellCode(self._parent_to, token)
            elif isinstance(token, PythonCodeToken):
                PythonCode(self._parent_to, token)
            elif isinstance(token, VimLCodeToken):
                VimLCode(self._parent_to, token)

###########################################################################
#                             Public classes                              #
###########################################################################
# TODO: this function is related to text object and should maybe be private
def _move_nocheck(obj, old_end, new_end, diff):
    assert(diff == (new_end - old_end)) # TODO: argument has no sense
    if obj < old_end: return
    debug("obj: %r, new_end: %r, diff: %r" % (obj, new_end, diff))
    if diff.line >= 0:
        obj.line += diff.line
        if obj.line == new_end.line:
            obj.col += diff.col
    else:
        debug("diff: %r" % (diff))
        obj.line += diff.line
        if obj.line == new_end.line:
            obj.col += diff.col

def _move(obj, sp, diff):
    if obj < sp: return

    debug("obj: %r, sp: %r, diff: %r" % (obj, sp, diff))
    if diff.line >= 0:
        if obj.line == sp.line:
            obj.col += diff.col
        obj.line += diff.line
    else:
        debug("diff: %r" % (diff))
        obj.line += diff.line
        if obj.line == sp.line:
            obj.col += sp.col

class TextObject(object):
    """
    This base class represents any object in the text
    that has a span in any ways
    """
    def __init__(self, parent, token, end = None, initial_text = ""):
        self._parent = parent

        ct = None
        if end is not None: # Took 4 arguments
            self._start = token
            self._end = end
            self._initial_text = TextBuffer(initial_text)
        else: # Initialize from token
            self._start = token.start
            self._end = token.end
            self._initial_text = TextBuffer(token.initial_text)

        self._childs = []
        self._tabstops = {}

        if parent is not None:
            parent._add_child(self)

        self._cts = 0
        self._is_killed = False # TODO: not often needed

    def initial_replace(self):
        ct = self._initial_text # TODO: Initial Text is nearly unused.
        debug("self._start: %r, self._end: %r" % (self._start, self._end))
        debug("self._start: %r, self._end: %r" % (self._start, self._end))
        debug("ct: %r" % (ct))
        old_end = self._end
        ct.to_vim(self._start, self._end) # TODO: to vim returns something unused
        debug("self._end: %r" % (self._end))
        self._end = ct.calc_end(self._start)
        debug("self._start: %r, self._end: %r" % (self._start, self._end))
        if self._end != old_end:
            exclude = set()
            exclude = set(c for c in self._childs)
            exclude.add(self)
            # TODO: maybe get rid of this function as well?
            self.child_end_moved(min(old_end, self._end), self._end - old_end, exclude)

    def __lt__(self, other):
        return self._start < other._start
    def __le__(self, other):
        return self._start <= other._start

    ##############
    # PROPERTIES #
    ##############
    @property
    def current_text(self):
        _span = self.span
        buf = vim.current.buffer

        if _span.start.line == _span.end.line:
            return as_unicode(buf[_span.start.line][_span.start.col:_span.end.col])
        else:
            lines = []
            lines.append(buf[_span.start.line][_span.start.col:])
            lines.extend(buf[_span.start.line+1:_span.end.line])
            lines.append(buf[_span.end.line][:_span.end.col])
            return as_unicode('\n'.join(lines))

    @property
    def current_tabstop(self):
        if self._cts is None:
            return None
        return self._tabstops[self._cts]

    def span(self):
        return Span(self._start, self._end)
    span = property(span)

    def start(self):
        return self._start
    start = property(start)

    def end(self):
        return self._end
    end = property(end)

    ####################
    # Public functions #
    ####################
    # TODO: This really only is called when a child has shortened
    def child_end_moved2(self, old_end, new_end): # TODO: pretty wasteful, give index
        if not (self._parent) or old_end == new_end:
            return

        debug("###*** ")
        assert(self._parent)
        _do_print(self._parent)

        pold_end = self._parent._end.copy()
        _move_nocheck(self._parent._end, old_end, new_end, new_end - old_end)
        def _move_all(o):
            _move_nocheck(o._start, old_end, new_end, new_end - old_end)
            _move_nocheck(o._end, old_end, new_end, new_end - old_end)

            for oc in o._childs:
                _move_all(oc)

        for c in self._parent._childs:
            if c is self: continue
            _move_all(c)
        _do_print(self._parent)
        debug("***### ")

        debug("pold_end: %r, self._parent._end: %r" % (pold_end, self._parent._end))
        self._parent.child_end_moved2(pold_end, self._parent._end)





    def child_end_moved(self, sp, diff, skip = set()): # TODO: pretty wasteful, give index
        debug("self: %r, skip: %r, diff: %r" % (self, skip, diff))

        if self not in skip:
            _move(self._end, sp, diff)

        for c in self._childs:
            if c in skip: continue
            def _move_all(o):
                _move(o._start, sp, diff)
                _move(o._end, sp, diff)

                for oc in o._childs:
                    _move_all(oc)
            _move_all(c)

        debug("self._parent: %r" % (self._parent))
        if self._parent and self._parent not in skip:
            debug("b4 parent sp: %r, diff: %r" % (sp, diff))
            self._parent.child_end_moved(sp, diff, set((self,)))
            debug("after parent sp: %r, diff: %r" % (sp, diff))

    def _do_edit(self, cmd):
        debug("self: %r, cmd: %r" % (self, cmd))
        ctype, line, col, char = cmd
        assert( ('\n' not in char) or (char == "\n"))
        pos = Position(line, col)

        to_kill = set()
        for c in self._childs:
            start = c._start
            end = c._end

            if ctype == "D":
                if char == "\n":
                    end_pos = Position(line + 1, 0) # TODO: is this even needed?
                else:
                    end_pos = pos + Position(0, len(char))
                # TODO: char is no longer true -> Text
                # Case: this deletion removes the child
                if (pos < start and end_pos >= end):
                    debug(" Case 1")
                    to_kill.add(c)
                # Case: this edit command is completely for the child
                elif (start <= pos <= end) and (start <= end_pos <= end):
                    debug(" Case 2")
                    if not isinstance(c, TabStop): # Erasing inside NonTabstop -> Kill element
                        to_kill.add(c)
                        continue
                    c._do_edit(cmd)
                    return
                # Case: partially for us, partially for the child
                elif (pos < start and (start < end_pos <= end)):
                    debug(" Case 3")
                    my_text = char[:(start-pos).col]
                    c_text = char[(start-pos).col:]
                    debug("    my_text: %r" % (my_text))
                    debug("    c_text: %r" % (c_text))
                    self._do_edit((ctype, line, col, my_text))
                    self._do_edit((ctype, line, col, c_text))
                    return
                elif (end_pos >= end and (start <= pos < end)):
                    debug(" Case 4")
                    c_text = char[(end-pos).col:]
                    my_text = char[:(end-pos).col]
                    debug("    c_text: %r" % (c_text))
                    debug("    my_text: %r" % (my_text))
                    self._do_edit((ctype, line, col, c_text))
                    self._do_edit((ctype, line, col, my_text))
                    return


            if ctype == "I":
                if not isinstance(c, TabStop): # TODO: make this nicer
                    continue
                if (start <= pos <= end):
                    c._do_edit(cmd)
                    return
        for c in to_kill:
            debug(" Kill c: %r" % (c))
            self._del_child(c)

        # We have to handle this ourselves
        if ctype == "D": # TODO: code duplication
            assert(self._start != self._end) # Makes no sense to delete in empty textobject

            if char == "\n":
                delta = Position(-1, col) # TODO: this feels somehow incorrect:
            else:
                delta = Position(0, -len(char))
        else:
            if char == "\n":
                delta = Position(1, -col) # TODO: this feels somehow incorrect
            else:
                delta = Position(0, len(char))
        old_end = self._end.copy()
        _move(self._end, Position(line, col), delta)
        #self.child_end_moved(Position(line, col), self._end - old_end, set((self,)))
        self.child_end_moved2(old_end, self._end)

    def edited(self, cmds): # TODO: Only in SnippetInstance
        assert(len([c for c in self._childs if isinstance(c, VimCursor)]) == 0)

        debug("begin: self.current_text: %r" % (self.current_text))
        debug("self._start: %r, self._end: %r" % (self._start, self._end))
        # Replay User Edits to update end of our current texts
        for cmd in cmds:
            self._do_edit(cmd)

        _do_print(self)

    def do_edits(self): # TODO: only in snippets instance
        debug("In do_edits")
        # Do our own edits; keep track of the Cursor
        vc = VimCursor(self)
        assert(len([c for c in self._childs if isinstance(c, VimCursor)]) == 1)
        # Update all referers # TODO: maybe in a function of its own
        def _update_non_tabstops(obj): # TODO: stupid functon name
            obj._really_updateman()

            for c in obj._childs:
                _update_non_tabstops(c)

        _update_non_tabstops(self)

        #debug("self._childs: %r, vc: %r" % (self._childs, vc))
        vc.update_position()
        self._del_child(vc)
        assert(len([c for c in self._childs if isinstance(c, VimCursor)]) == 0)
        debug("self._childs: %r" % (self._childs))

        _do_print(self)


    def update(self):
        pass # TODO: remove this function
        # def _update_childs(childs):
            # for idx,c in childs:
                # oldend = Position(c.end.line, c.end.col)

                # new_end = c.update()

                # moved_lines = new_end.line - oldend.line
                # moved_cols = new_end.col - oldend.col

                # self._current_text.replace_text(c.start, oldend, c._current_text)

                # self._move_textobjects_behind(c.start, oldend, moved_lines,
                            # moved_cols, idx)

        # _update_childs((idx, c) for idx, c in enumerate(self._childs) if isinstance(c, TabStop))
        # _update_childs((idx, c) for idx, c in enumerate(self._childs) if not isinstance(c, TabStop))

        # self._do_update()

        # new_end = self._current_text.calc_end(self._start)

        # self._end = new_end

        # return new_end

    def _get_next_tab(self, no):
        debug("_get_next_tab: self: %r, no: %r" % (self, no))
        if not len(self._tabstops.keys()):
            return
        tno_max = max(self._tabstops.keys())

        possible_sol = []
        i = no + 1
        while i <= tno_max:
            if i in self._tabstops:
                possible_sol.append( (i, self._tabstops[i]) )
                break
            i += 1

        c = [ c._get_next_tab(no) for c in self._childs ]
        c = filter(lambda i: i, c)

        possible_sol += c

        if not len(possible_sol):
            return None

        return min(possible_sol)


    def _get_prev_tab(self, no):
        if not len(self._tabstops.keys()):
            return
        tno_min = min(self._tabstops.keys())

        possible_sol = []
        i = no - 1
        while i >= tno_min and i > 0:
            if i in self._tabstops:
                possible_sol.append( (i, self._tabstops[i]) )
                break
            i -= 1

        c = [ c._get_prev_tab(no) for c in self._childs ]
        c = filter(lambda i: i, c)

        possible_sol += c

        if not len(possible_sol):
            return None

        return max(possible_sol)

    ###############################
    # Private/Protected functions #
    ###############################
    def _really_updateman(self): # TODO:
        pass

    # def _move_textobjects_behind(self, start, end, lines, cols, obj_idx):
        # if lines == 0 and cols == 0:
            # return

        # for idx,m in enumerate(self._childs[obj_idx+1:]):
            # delta_lines = 0
            # delta_cols_begin = 0
            # delta_cols_end = 0

            # if m.start.line > end.line:
                # delta_lines = lines
            # elif m.start.line == end.line:
                # if m.start.col >= end.col:
                    # if lines:
                        # delta_lines = lines
                    # delta_cols_begin = cols
                    # if m.start.line == m.end.line:
                        # delta_cols_end = cols
            # m.start.line += delta_lines
            # m.end.line += delta_lines
            # m.start.col += delta_cols_begin
            # m.end.col += delta_cols_end

    def _get_tabstop(self, requester, no):
        if no in self._tabstops:
            return self._tabstops[no]
        for c in self._childs:
            if c is requester:
                continue

            rv = c._get_tabstop(self, no)
            if rv is not None:
                return rv
        if self._parent and requester is not self._parent:
            return self._parent._get_tabstop(self, no)

    def _add_child(self,c):
        self._childs.append(c)
        self._childs.sort()

    def _del_child(self,c):
        c._is_killed = True # TODO: private parts
        debug("len(self._childs): %r, self._childs: %r" % (len(self._childs), self._childs))
        self._childs.remove(c)
        debug("len(self._childs): %r, self._childs: %r" % (len(self._childs), self._childs))

        if isinstance(c, TabStop):
            del self._tabstops[c.no]

    def _add_tabstop(self, ts):
        self._tabstops[ts.no] = ts

class EscapedChar(TextObject):
    """
    This class is a escape char like \$. It is handled in a text object
    to make sure that remaining children are correctly moved after
    replacing the text.

    This is a base class without functionality just to mark it in the code.
    """
    pass

class VimCursor(TextObject):
    def __init__(self, parent):
        line, col = vim.current.window.cursor # TODO: some schenanigans like col -> byte?
        s = Position(line-1, col)
        e = Position(line-1, col)
        TextObject.__init__(self, parent, s, e)

    def update_position(self):
        assert(self._start == self._end)
        vim.current.window.cursor = (self._start.line + 1, self._start.col)

    def __repr__(self):
        return "VimCursor(%r)" % (self._start)

# TODO: Maybe DependantTextObject which can't be edited and can be killed
class Mirror(TextObject):
    """
    A Mirror object mirrors a TabStop that is, text is repeated here
    """
    def __init__(self, parent, tabstop, token):
        TextObject.__init__(self, parent, token)

        self._ts = tabstop

    def _really_updateman(self): # TODO: function has a stupid name
        # TODO: this function will get called to often. It should
        # check if a replacement is really needed
        assert(not self._is_killed)

        if self._ts._is_killed:
            tb = TextBuffer("")
        else:

            tb = TextBuffer(self._ts.current_text)
        debug("new_text, self: %r" % (self))
        debug("tb: %r" % (tb))
        debug("self._start: %r, self._end: %r, self.current_text: %r" % (self._start, self._end, self.current_text))
        # TODO: initial replace does not need to take an argument
        old_end = self._end
        tb.to_vim(self._start, self._end) # TODO: to vim returns something unused
        new_end = tb.calc_end(self._start)
        self._end = new_end
        if new_end != old_end:
            # TODO: child_end_moved is a stupid name for this function
            self.child_end_moved2(old_end, new_end)

        if self._ts._is_killed:
            self._parent._del_child(self)

    def __repr__(self):
        return "Mirror(%s -> %s, %r)" % (self._start, self._end, self.current_text)

class Visual(TextObject):
    """
    A ${VISUAL} placeholder that will use the text that was last visually
    selected and insert it here. If there was no text visually selected,
    this will be the empty string
    """
    def __init__(self, parent, token):

        # Find our containing snippet for visual_content
        snippet = parent
        while snippet and not isinstance(snippet, SnippetInstance):
            snippet = snippet._parent

        text = ""
        for idx, line in enumerate(snippet.visual_content.splitlines(True)):
            text += token.leading_whitespace
            text += line

        self._text = text

        TextObject.__init__(self, parent, token, initial_text = self._text)

    def _do_update(self):
        self.current_text = self._text

    def __repr__(self):
        return "Visual(%s -> %s)" % (self._start, self._end)


class Transformation(Mirror):
    def __init__(self, parent, ts, token):
        Mirror.__init__(self, parent, ts, token)

        flags = 0
        self._match_this_many = 1
        if token.options:
            if "g" in token.options:
                self._match_this_many = 0
            if "i" in token.options:
                flags |=  re.IGNORECASE

        self._find = re.compile(token.search, flags | re.DOTALL)
        self._replace = _CleverReplace(token.replace)

    def _do_update(self):
        t = self._ts.current_text
        t = self._find.subn(self._replace.replace, t, self._match_this_many)[0]
        self.current_text = t

    def __repr__(self):
        return "Transformation(%s -> %s)" % (self._start, self._end)

class ShellCode(TextObject):
    def __init__(self, parent, token):
        code = token.code.replace("\\`", "`")

        # Write the code to a temporary file
        handle, path = tempfile.mkstemp(text=True)
        os.write(handle, code.encode("utf-8"))
        os.close(handle)

        os.chmod(path, stat.S_IRWXU)

        # Interpolate the shell code. We try to stay as compatible with Python
        # 2.3, therefore, we do not use the subprocess module here
        output = os.popen(path, "r").read()
        if len(output) and output[-1] == '\n':
            output = output[:-1]
        if len(output) and output[-1] == '\r':
            output = output[:-1]

        os.unlink(path)

        token.initial_text = output
        TextObject.__init__(self, parent, token)

    def __repr__(self):
        return "ShellCode(%s -> %s)" % (self._start, self._end)

class VimLCode(TextObject):
    def __init__(self, parent, token):
        self._code = token.code.replace("\\`", "`").strip()

        TextObject.__init__(self, parent, token)

    def _do_update(self):
        self.current_text = as_unicode(vim.eval(self._code))

    def __repr__(self):
        return "VimLCode(%s -> %s)" % (self._start, self._end)

class _Tabs(object):
    def __init__(self, to):
        self._to = to

    def __getitem__(self, no):
        ts = self._to._get_tabstop(self._to, int(no))
        if ts is None:
            return ""
        return ts.current_text

class SnippetUtil(object):
    """ Provides easy access to indentation, etc.
    """

    def __init__(self, initial_indent, cur=""):
        self._ind = IndentUtil()

        self._initial_indent = self._ind.indent_to_spaces(initial_indent)

        self._reset(cur)

    def _reset(self, cur):
        """ Gets the snippet ready for another update.

        :cur: the new value for c.
        """
        self._ind.reset()
        self._c = cur
        self._rv = ""
        self._changed = False
        self.reset_indent()

    def shift(self, amount=1):
        """ Shifts the indentation level.
        Note that this uses the shiftwidth because thats what code
        formatters use.

        :amount: the amount by which to shift.
        """
        self.indent += " " * self._ind.sw * amount

    def unshift(self, amount=1):
        """ Unshift the indentation level.
        Note that this uses the shiftwidth because thats what code
        formatters use.

        :amount: the amount by which to unshift.
        """
        by = -self._ind.sw * amount
        try:
            self.indent = self.indent[:by]
        except IndexError:
            indent = ""

    def mkline(self, line="", indent=None):
        """ Creates a properly set up line.

        :line: the text to add
        :indent: the indentation to have at the beginning
                 if None, it uses the default amount
        """
        if indent == None:
            indent = self.indent
            # this deals with the fact that the first line is
            # already properly indented
            if '\n' not in self._rv:
                try:
                    indent = indent[len(self._initial_indent):]
                except IndexError:
                    indent = ""
            indent = self._ind.spaces_to_indent(indent)

        return indent + line

    def reset_indent(self):
        """ Clears the indentation. """
        self.indent = self._initial_indent

    # Utility methods
    @property
    def fn(self):
        """ The filename. """
        return vim.eval('expand("%:t")') or ""

    @property
    def basename(self):
        """ The filename without extension. """
        return vim.eval('expand("%:t:r")') or ""

    @property
    def ft(self):
        """ The filetype. """
        return self.opt("&filetype", "")

    # Necessary stuff
    def rv():
        """ The return value.
        This is a list of lines to insert at the
        location of the placeholder.

        Deprecates res.
        """
        def fget(self):
            return self._rv
        def fset(self, value):
            self._changed = True
            self._rv = value
        return locals()
    rv = property(**rv())

    @property
    def _rv_changed(self):
        """ True if rv has changed. """
        return self._changed

    @property
    def c(self):
        """ The current text of the placeholder.

        Deprecates cur.
        """
        return self._c

    def opt(self, option, default=None):
        """ Gets a vim variable. """
        if vim.eval("exists('%s')" % option) == "1":
            try:
                return vim.eval(option)
            except vim.error:
                pass
        return default

    # Syntatic sugar
    def __add__(self, value):
        """ Appends the given line to rv using mkline. """
        self.rv += '\n' # handles the first line properly
        self.rv += self.mkline(value)
        return self

    def __lshift__(self, other):
        """ Same as unshift. """
        self.unshift(other)

    def __rshift__(self, other):
        """ Same as shift. """
        self.shift(other)


class PythonCode(TextObject):
    def __init__(self, parent, token):

        code = token.code.replace("\\`", "`")

        # Find our containing snippet for snippet local data
        snippet = parent
        while snippet and not isinstance(snippet, SnippetInstance):
            try:
                snippet = snippet._parent
            except AttributeError:
                snippet = None
        self._snip = SnippetUtil(token.indent)
        self._locals = snippet.locals

        self._globals = {}
        globals = snippet.globals.get("!p", [])
        compatible_exec("\n".join(globals).replace("\r\n", "\n"), self._globals)

        # Add Some convenience to the code
        self._code = "import re, os, vim, string, random\n" + code

        TextObject.__init__(self, parent, token)


    def _do_update(self):
        path = vim.eval('expand("%")')
        if path is None:
            path = ""
        fn = os.path.basename(path)

        ct = self.current_text
        self._snip._reset(ct)
        local_d = self._locals

        local_d.update({
            't': _Tabs(self),
            'fn': fn,
            'path': path,
            'cur': ct,
            'res': ct,
            'snip' : self._snip,
        })

        self._code = self._code.replace("\r\n", "\n")
        compatible_exec(self._code, self._globals, local_d)

        if self._snip._rv_changed:
            self.current_text = self._snip.rv
        else:
            self.current_text = as_unicode(local_d["res"])

    def __repr__(self):
        return "PythonCode(%s -> %s)" % (self._start, self._end)

class TabStop(TextObject):
    """
    This is the most important TextObject. A TabStop is were the cursor
    comes to rest when the user taps through the Snippet.
    """
    def __init__(self, parent, token, start = None, end = None):
        if start is not None:
            self._no = token
            TextObject.__init__(self, parent, start, end)
        else:
            TextObject.__init__(self, parent, token)
            self._no = token.no

    def no(self):
        return self._no
    no = property(no)

    # TODO: none of the _repr_ must access _current_text
    def __repr__(self):
        return "TabStop(%i, %s -> %s, %s)" % (self._no, self._start, self._end,
            repr(self.current_text))

class SnippetInstance(TextObject):
    """
    A Snippet instance is an instance of a Snippet Definition. That is,
    when the user expands a snippet, a SnippetInstance is created to
    keep track of the corresponding TextObjects. The Snippet itself is
    also a TextObject because it has a start an end
    """

    def __init__(self, parent, indent, initial_text, start, end, visual_content, last_re, globals):
        if start is None:
            start = Position(0,0)
        if end is None:
            end = Position(0,0)

        self.locals = {"match" : last_re}
        self.globals = globals
        self.visual_content = visual_content

        TextObject.__init__(self, parent, start, end, initial_text)

        _TOParser(self, initial_text, indent).parse(True)

        _do_print(self)

    def __repr__(self):
        return "SnippetInstance(%s -> %s, %r)" % (self._start, self._end, self.current_text)

    def _get_tabstop(self, requester, no):
        # SnippetInstances are completely self contained, therefore, we do not
        # need to ask our parent for Tabstops
        p = self._parent
        self._parent = None
        rv = TextObject._get_tabstop(self, requester, no)
        self._parent = p

        return rv

    def select_next_tab(self, backwards = False):
        debug("select_next_tab: self: %r, self._cts: %r" % (self, self._cts))
        if self._cts is None:
            return

        if backwards:
            cts_bf = self._cts

            res = self._get_prev_tab(self._cts)
            if res is None:
                self._cts = cts_bf
                return self._tabstops[self._cts]
            self._cts, ts = res
            return ts
        else:
            res = self._get_next_tab(self._cts)
            if res is None:
                self._cts = None
                return self._tabstops[0]
            else:
                self._cts, ts = res
                return ts

        return self._tabstops[self._cts]
