#!/usr/bin/python
"""Single-track AppKit shortcut experiment for Mac OS X 10.6."""

import json
import os
import sys
import threading
import time

from AppKit import (
    NSApplication, NSBackingStoreBuffered,
    NSClosableWindowMask, NSColor, NSFloatingWindowLevel, NSFont,
    NSFontAttributeName, NSForegroundColorAttributeName, NSMakePoint, NSMakeRect, NSRectFill,
    NSShiftKeyMask, NSTitledWindowMask, NSView, NSWindow,
)
from Foundation import NSString
from Quartz import (
    CGDisplayPixelsHigh, CGEventCreate, CGEventCreateMouseEvent, CGEventGetLocation,
    CGEventPost, CGEventSetFlags, CGMainDisplayID, CGWindowListCopyWindowInfo,
    kCGEventLeftMouseDown, kCGEventLeftMouseDragged, kCGEventLeftMouseUp,
    kCGEventMouseMoved, kCGHIDEventTap, kCGMouseButtonLeft, kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)


def scratch_live_at(point):
    """Refuse a simulated drop if a different application covers its target."""
    x, y = point
    for window in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID):
        if window.get("kCGWindowLayer") != 0:
            continue
        bounds = window["kCGWindowBounds"]
        if (bounds["X"] <= x < bounds["X"] + bounds["Width"]
                and bounds["Y"] <= y < bounds["Y"] + bounds["Height"]):
            return window.get("kCGWindowOwnerName", "").lower() == "scratch live"
    return False


def pointer_event(kind, point):
    event = CGEventCreateMouseEvent(None, kind, point, kCGMouseButtonLeft)
    CGEventSetFlags(event, 0)
    CGEventPost(kCGHIDEventTap, event)


def move_file(start, target, previous, ready):
    time.sleep(0.15)
    pointer_event(kCGEventMouseMoved, start)
    pointer_event(kCGEventLeftMouseDown, start)
    try:
        # Wait for the AppKit view to receive mouseDown and offer the actual file.
        ready.wait(1)
        if not ready.isSet():
            return
        time.sleep(0.12)
        for index in range(1, 25):
            fraction = index / 24.0
            point = tuple(a + (b - a) * fraction for a, b in zip(start, target))
            pointer_event(kCGEventLeftMouseDragged, point)
            time.sleep(0.035)
        time.sleep(0.15)
    finally:
        pointer_event(kCGEventLeftMouseUp, target if ready.isSet() else start)
        time.sleep(0.2)
        pointer_event(kCGEventMouseMoved, previous)


def set_status(view, value):
    view.status = value
    sys.stdout.write(value + "\n")
    sys.stdout.flush()
    view.setNeedsDisplay_(True)


class ShortcutView(NSView):
    def acceptsFirstResponder(self):
        return True

    def drawRect_(self, _rect):
        NSColor.windowBackgroundColor().set()
        NSRectFill(self.bounds())
        styles = (
            (self.track_name, 15, 70),
            (u"Shift + Left: load left deck    Shift + Right: load right deck", 12, 43),
            (self.status, 11, 20),
        )
        for title, size, y in styles:
            attributes = {
                NSFontAttributeName: NSFont.systemFontOfSize_(size),
                NSForegroundColorAttributeName: NSColor.controlTextColor(),
            }
            NSString.stringWithString_(title).drawAtPoint_withAttributes_(NSMakePoint(16, y), attributes)

    def keyDown_(self, event):
        names = {123: "left", 124: "right"}
        side = names.get(event.keyCode())
        if not side or not (event.modifierFlags() & NSShiftKeyMask):
            return
        if event.isARepeat() or self.pending is not None:
            return
        target = tuple(self.targets[side])
        if not os.path.isfile(self.track):
            set_status(self, "The local test file is missing.")
            return
        if not scratch_live_at(target):
            set_status(self, "Scratch Live must be visible at the calibrated " + side + " deck.")
            return
        point = self.convertPoint_toView_(NSMakePoint(28, 73), None)
        point = self.window().convertBaseToScreen_(point)
        start = (point.x, CGDisplayPixelsHigh(CGMainDisplayID()) - point.y)
        cursor = CGEventGetLocation(CGEventCreate(None))
        ready = threading.Event()
        self.pending = (side, ready)
        set_status(self, "Sending the file to the " + side + " deck...")
        thread = threading.Thread(target=move_file, args=(start, target, (cursor.x, cursor.y), ready))
        thread.daemon = True
        thread.start()

    def mouseDown_(self, event):
        if self.pending is None:
            return
        side, ready = self.pending
        ready.set()
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        try:
            dropped = self.dragFile_fromRect_slideBack_event_(
                self.track, NSMakeRect(point.x - 8, point.y - 8, 16, 16), False, event
            )
            message = "File drop sent to the " + side + " deck." if dropped else "File drop did not complete."
            set_status(self, message)
        finally:
            self.pending = None


def main():
    config = json.load(open(sys.argv[1]))
    app = NSApplication.sharedApplication()
    screen_height = CGDisplayPixelsHigh(CGMainDisplayID())
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(755, screen_height - 450, 485, 105),
        NSTitledWindowMask | NSClosableWindowMask, NSBackingStoreBuffered, False,
    )
    window.setTitle_("Music Stream - Deck Shortcut Prototype")
    window.setLevel_(NSFloatingWindowLevel)
    window.setReleasedWhenClosed_(False)
    view = ShortcutView.alloc().initWithFrame_(window.contentView().bounds())
    view.track = config["track"]
    view.track_name = os.path.basename(view.track)
    view.targets = config["targets"]
    view.pending = None
    view.status = "Ready - use an idle deck"
    window.setContentView_(view)
    window.makeFirstResponder_(view)
    window.makeKeyAndOrderFront_(None)
    app.activateIgnoringOtherApps_(True)
    print("PID %d" % os.getpid())
    sys.stdout.flush()
    app.run()


if __name__ == "__main__":
    main()
