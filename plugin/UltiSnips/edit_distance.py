#!/usr/bin/env python
# encoding: utf-8

import heapq # TODO: overkill. Bucketing is better
from collections import defaultdict
import sys

class GridPoint(object):
    """Docstring for GridPoint """

    __slots__ = ("parent", "cost",)

    def __init__(self, parent, cost):
        """@todo: to be defined

        :parent: @todo
        :cost: @todo
        """
        self._parent = parent
        self._cost = cost

def edit_script(a, b):
    d = defaultdict(list)
    seen = defaultdict(lambda: sys.maxint)

    d[0] = [ (0,0,0,0, ()) ]

    # TODO: needs some doku
    cost = 0
    DI_COST = 1000 # len(a)+len(b) Fix this up
    while True:
        while len(d[cost]):
            #sumarized = [ compactify(what) for c, x, line, col, what in d[cost] ] # TODO: not needed
            #print "%r: %r" % (cost, sumarized)
            x, y, line, col, what = d[cost].pop()

            if a[x:] == b[y:]: ## TODO: try out is
                #print "cost: %r" % (cost)
                return what

            if x < len(a) and y < len(b) and a[x] == b[y]:
                ncol = col + 1
                nline = line
                if a[x] == '\n':
                    ncol = 0
                    nline +=1
                if seen[x+1,y+1] > cost + len(a) - x:
                    d[cost + len(a) - x].append((x+1,y+1, nline, ncol, what)) # TODO: slow!
                    seen[x+1,y+1] = cost + len(a) - x
            if y < len(b):
                ncol = col + 1
                nline = line
                if b[y] == '\n':
                    ncol = 0
                    nline += 1
                if seen[x,y+1] > cost + DI_COST:
                    seen[x,y+1] = cost + DI_COST
                    d[cost + DI_COST].append((x,y+1, nline, ncol, what + (("I", line, col,b[y]),)))
            if x < len(a):
                if seen[x+1,y] > cost + DI_COST:
                    seen[x+1,y] = cost + DI_COST
                    d[cost + DI_COST].append((x+1,y, line, col, what + (("D",line, col, a[x]),) ))
        cost += 1

def compactify(es):
    cmds = []
    for cmd in es:
        ctype, line, col, char = cmd
        if (cmds and ctype == "D" and cmds[-1][1] == cmd[1] and cmds[-1][2] == cmd[2] and char != '\n'):
            cmds[-1][-1] += char
        elif (cmds and ctype == "I" and cmds[-1][1] == cmd[1] and cmds[-1][2]+1 == cmd[2] and char != '\n'):
            cmds[-1][-1] += char
        else:
            cmds.append(list(cmd))
    return cmds

def transform(a, cmds):
    buf = a.split("\n")

    for cmd in cmds:
        ctype, line, col, char = cmd
        if ctype == "D":
            buf[line] = buf[line][:col] + buf[line][col+1:]
        elif ctype == "I":
            buf[line] = buf[line][:col] + char + buf[line][col:]
        buf = '\n'.join(buf).split('\n')
    return '\n'.join(buf)


import unittest

class _Base(object):
    def runTest(self):
        es = edit_script(self.a, self.b)
        print "compactify(es: %r" % (compactify(es))
        tr = transform(self.a, es)
        self.assertEqual(self.b, tr)

# class TestEmptyString(_Base, unittest.TestCase):
    # a, b = "", ""

# class TestAllMatch(_Base, unittest.TestCase):
    # a, b = "abcdef", "abcdef"

# class TestLotsaNewlines(_Base, unittest.TestCase):
    # a, b = "Hello", "Hello\nWorld\nWorld\nWorld"

# class TestCrash(_Base, unittest.TestCase):
    # a = 'hallo Blah mitte=sdfdsfsd\nhallo kjsdhfjksdhfkjhsdfkh mittekjshdkfhkhsdfdsf'
    # b = 'hallo Blah mitte=sdfdsfsd\nhallo b mittekjshdkfhkhsdfdsf'

# class TestRealLife(_Base, unittest.TestCase):
    # a = 'hallo End Beginning'
    # b = 'hallo End t'

class TestRealLife1(_Base, unittest.TestCase):
    a = 'Vorne hallo Hinten'
    b = 'Vorne hallo  Hinten'
    # def test_all_match(self):
        # rv = edit_script("abcdef", "abcdef")
        # self.assertEqual("MMMMMM", rv)

    # def test_no_substr(self):
        # rv = edit_script("abc", "def")
        # self.assertEqual("SSS", rv)

    # def test_paper_example(self):
        # rv = edit_script("abcabba","cbabac")
        # self.assertEqual(rv, "SMDMMDMI")

    # def test_skiena_example(self):
        # rv = edit_script("thou shalt not", "you should not")
        # self.assertEqual(rv, "DSMMMMMISMSMMMM")
if __name__ == '__main__':
   unittest.main()
   # k = TestEditScript()
   # unittest.TextTestRunner().run(k)



