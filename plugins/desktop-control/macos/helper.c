// desktop-mcp helper: display geometry, scroll wheel events and TCC prompts.
// Needs no permissions of its own for geometry; the capture/input grants live on
// whatever process is responsible for it.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ApplicationServices/ApplicationServices.h>

static int cmd_displays(void) {
    CGDirectDisplayID ids[16];
    uint32_t n = 0;
    if (CGGetActiveDisplayList(16, ids, &n) != kCGErrorSuccess) return 1;
    CGDirectDisplayID main = CGMainDisplayID();
    printf("[");
    for (uint32_t i = 0; i < n; i++) {
        CGRect b = CGDisplayBounds(ids[i]);
        CGDisplayModeRef m = CGDisplayCopyDisplayMode(ids[i]);
        size_t pw = m ? CGDisplayModeGetPixelWidth(m) : 0;
        size_t ph = m ? CGDisplayModeGetPixelHeight(m) : 0;
        if (m) CGDisplayModeRelease(m);
        printf("%s{\"index\":%u,\"id\":%u,\"main\":%s,"
               "\"origin\":[%.0f,%.0f],\"logical\":[%.0f,%.0f],\"pixels\":[%zu,%zu]}",
               i ? "," : "", i + 1, (unsigned)ids[i],
               ids[i] == main ? "true" : "false",
               b.origin.x, b.origin.y, b.size.width, b.size.height, pw, ph);
    }
    printf("]\n");
    return 0;
}

static int cmd_scroll(double x, double y, int dx, int dy) {
    if (x >= 0 && y >= 0) {
        CGEventRef mv = CGEventCreateMouseEvent(NULL, kCGEventMouseMoved,
                                                CGPointMake(x, y), kCGMouseButtonLeft);
        CGEventPost(kCGHIDEventTap, mv);
        CFRelease(mv);
        usleep(30000);
    }
    CGEventRef ev = CGEventCreateScrollWheelEvent(NULL, kCGScrollEventUnitPixel, 2,
                                                  (int32_t)dy, (int32_t)dx);
    if (!ev) return 1;
    CGEventPost(kCGHIDEventTap, ev);
    CFRelease(ev);
    return 0;
}

// Registers the app in the Screen Recording pane and raises the dialog. Without
// this the app never appears in the list and has to be added by hand with "+".
static int cmd_screenaccess(void) {
    int pre = CGPreflightScreenCaptureAccess();
    int req = pre ? 1 : CGRequestScreenCaptureAccess();
    printf("{\"screen_capture_before\":%s,\"screen_capture_after\":%s}\n",
           pre ? "true" : "false", req ? "true" : "false");
    return 0;
}

// Same for Accessibility. Note this only opens System Settings: the switch
// itself has to be flipped by a human, a dialog cannot grant it.
static int cmd_axprompt(void) {
    const void *keys[] = {kAXTrustedCheckOptionPrompt};
    const void *vals[] = {kCFBooleanTrue};
    CFDictionaryRef opts = CFDictionaryCreate(NULL, keys, vals, 1,
                                              &kCFTypeDictionaryKeyCallBacks,
                                              &kCFTypeDictionaryValueCallBacks);
    int trusted = AXIsProcessTrustedWithOptions(opts);
    CFRelease(opts);
    printf("{\"accessibility_trusted\":%s}\n", trusted ? "true" : "false");
    return 0;
}

static int cmd_trusted(void) {
    printf("{\"accessibility_trusted\":%s}\n", AXIsProcessTrusted() ? "true" : "false");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: helper displays|scroll dx dy [x y]|trusted\n"); return 2; }
    if (!strcmp(argv[1], "displays")) return cmd_displays();
    if (!strcmp(argv[1], "trusted"))      return cmd_trusted();
    if (!strcmp(argv[1], "screenaccess")) return cmd_screenaccess();
    if (!strcmp(argv[1], "axprompt"))     return cmd_axprompt();
    if (!strcmp(argv[1], "scroll") && argc >= 4) {
        double x = argc >= 6 ? atof(argv[4]) : -1, y = argc >= 6 ? atof(argv[5]) : -1;
        return cmd_scroll(x, y, atoi(argv[2]), atoi(argv[3]));
    }
    fprintf(stderr, "bad args\n");
    return 2;
}
