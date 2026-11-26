"""The keyboard, as Windows itself records it, must not be left on the hidden window of a page.

In programs built on Chromium (browsers, Electron apps such as the Claude app) the main window has the keyboard; every
page inside it has a hidden window of its own that never does. UI Automation's SetFocus() leaves the keyboard on that
hidden window, and from then on UI Automation answers "what has the keyboard?" from that page's point of view, also
after the user has clicked into another page of the same window. Found 2026-11-26: after a conversion during which the
user had clicked into the browser pane, the next Alt+1 in the chat box was refused ("not an input box").
usage: test_native_focus.py <app dir>"""
import sys

from _app import APP
import inputbox as ib

MAIN, PAGE, OTHER_MAIN, EDIT = 0x3081A, 0x402B0, 0x222, 0x111
world = {"focus": PAGE,
         "classes": {PAGE: "Chrome_RenderWidgetHostHWND", MAIN: "Chrome_WidgetWin_1", EDIT: "Edit", OTHER_MAIN: "Notepad"},
         "parents": {PAGE: MAIN, EDIT: OTHER_MAIN}}
given = []


def give(window):
    given.append(window)
    world["focus"] = window
    return True


ib._keyboard_window = lambda window: world["focus"]
ib._window_class = lambda window: world["classes"].get(window, "")
ib._parent_window = lambda window: world["parents"].get(window)
ib._give_keyboard = give

# 1. a page's hidden window has the keyboard: it goes back to the window the page is in
assert ib.settle_native_focus(MAIN) is True and given == [MAIN] and world["focus"] == MAIN, given
print("1 OK the keyboard is taken off a page's hidden window and given back to the main window")

# 2. nothing to put right: nothing is touched
del given[:]
assert ib.settle_native_focus(MAIN) is False and given == []
world["focus"] = EDIT  # an ordinary program: its text box has a window of its own, and that is where the keyboard belongs
assert ib.settle_native_focus(OTHER_MAIN) is False and given == [] and world["focus"] == EDIT
world["focus"] = None
assert ib.settle_native_focus(MAIN) is False and given == []
world["focus"], world["parents"] = PAGE, {}  # a page window without a parent (cannot be): left alone
assert ib.settle_native_focus(MAIN) is False and given == []
print("2 OK the main window, an ordinary program's own text box and odd cases are left alone")

# 3. the session: it is put right before the first question of a look, and after each time the program moves the keyboard itself
order = []
ib.settle_native_focus = lambda window=None: order.append("settle") or False
session = ib.Session.__new__(ib.Session)
session._silent, session.seen, session._box, session._away, session._automation = False, None, None, None, None
session._ask = lambda function, timeout=1.5: order.append("ask") or None
session.focus_box()
assert order == ["ask", "settle"], order
del order[:]
session.focus_away()
assert order == ["ask", "settle"], order
del order[:]
session.look(tries=2, pause=0)
assert order == ["settle", "ask", "ask"], order
print("3 OK a look starts from a settled keyboard; going to the box and going back each settle it again")
print("ALL OK")
