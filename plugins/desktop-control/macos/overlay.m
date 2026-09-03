// overlay - visible notice that this desktop is under remote control.
//
// One borderless window per screen, CLICK-THROUGH (ignoresMouseEvents) so it never
// blocks the automation it is announcing, above everything, with a pulsing blue
// gradient glow on all four edges and a banner on top.
//
// Deliberately a separate binary from the app bundle: the bundle is ad-hoc signed,
// and recompiling it invalidates its TCC grants (the switch stays on but dead).
#import <Cocoa/Cocoa.h>
#import <QuartzCore/QuartzCore.h>

static NSMutableArray *gWindows;
static NSString *gText;

static CAGradientLayer *edge(CGRect r, NSPoint start, NSPoint end) {
    CAGradientLayer *g = [CAGradientLayer layer];
    g.frame = r;
    g.startPoint = CGPointMake(start.x, start.y);
    g.endPoint = CGPointMake(end.x, end.y);
    g.colors = @[(id)[NSColor colorWithSRGBRed:0.16 green:0.55 blue:1.0 alpha:0.50].CGColor,
                 (id)[NSColor colorWithSRGBRed:0.16 green:0.55 blue:1.0 alpha:0.18].CGColor,
                 (id)[NSColor colorWithSRGBRed:0.16 green:0.55 blue:1.0 alpha:0.0].CGColor];
    g.locations = @[@0.0, @0.35, @1.0];
    CABasicAnimation *pulse = [CABasicAnimation animationWithKeyPath:@"opacity"];
    pulse.fromValue = @0.55;
    pulse.toValue = @1.0;
    pulse.duration = 1.6;
    pulse.autoreverses = YES;
    pulse.repeatCount = HUGE_VALF;
    pulse.timingFunction = [CAMediaTimingFunction functionWithName:kCAMediaTimingFunctionEaseInEaseOut];
    [g addAnimation:pulse forKey:@"pulse"];
    return g;
}

static NSView *banner(CGFloat screenW) {
    NSFont *font = [NSFont systemFontOfSize:15 weight:NSFontWeightSemibold];
    NSDictionary *attrs = @{NSFontAttributeName: font};
    NSSize ts = [gText sizeWithAttributes:attrs];
    CGFloat w = MIN(screenW - 80, ts.width + 40);
    CGFloat h = 34;

    NSView *pill = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, w, h)];
    pill.wantsLayer = YES;
    pill.layer.cornerRadius = h / 2;
    pill.layer.backgroundColor = [NSColor colorWithSRGBRed:0.06 green:0.31 blue:0.72 alpha:0.92].CGColor;
    pill.layer.borderWidth = 1.0;
    pill.layer.borderColor = [NSColor colorWithSRGBRed:0.45 green:0.75 blue:1.0 alpha:0.9].CGColor;
    pill.layer.shadowColor = [NSColor colorWithSRGBRed:0.16 green:0.55 blue:1.0 alpha:1.0].CGColor;
    pill.layer.shadowRadius = 14;
    pill.layer.shadowOpacity = 0.9;
    pill.layer.shadowOffset = CGSizeZero;

    NSTextField *lbl = [[NSTextField alloc] initWithFrame:NSMakeRect(20, (h - 20) / 2, w - 40, 20)];
    lbl.stringValue = gText;
    lbl.font = font;
    lbl.textColor = [NSColor whiteColor];
    lbl.bezeled = NO;
    lbl.editable = NO;
    lbl.selectable = NO;
    lbl.drawsBackground = NO;
    lbl.lineBreakMode = NSLineBreakByTruncatingMiddle;
    [pill addSubview:lbl];
    return pill;
}

static void build(void) {
    for (NSWindow *w in gWindows) [w orderOut:nil];
    [gWindows removeAllObjects];

    for (NSScreen *scr in [NSScreen screens]) {
        NSRect f = [scr frame];
        CGFloat W = f.size.width, H = f.size.height, G = 70;

        NSWindow *win = [[NSWindow alloc] initWithContentRect:f
                                                    styleMask:NSWindowStyleMaskBorderless
                                                      backing:NSBackingStoreBuffered
                                                        defer:NO
                                                       screen:scr];
        win.opaque = NO;
        win.backgroundColor = [NSColor clearColor];
        win.hasShadow = NO;
        win.ignoresMouseEvents = YES;   // essential: otherwise it eats the clicks
        win.level = NSScreenSaverWindowLevel;
        win.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces |
                                 NSWindowCollectionBehaviorStationary |
                                 NSWindowCollectionBehaviorFullScreenAuxiliary |
                                 NSWindowCollectionBehaviorIgnoresCycle;

        NSView *v = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, W, H)];
        v.wantsLayer = YES;
        [v.layer addSublayer:edge(CGRectMake(0, H - G, W, G), NSMakePoint(0.5, 1), NSMakePoint(0.5, 0))];
        [v.layer addSublayer:edge(CGRectMake(0, 0, W, G), NSMakePoint(0.5, 0), NSMakePoint(0.5, 1))];
        [v.layer addSublayer:edge(CGRectMake(0, 0, G, H), NSMakePoint(0, 0.5), NSMakePoint(1, 0.5))];
        [v.layer addSublayer:edge(CGRectMake(W - G, 0, G, H), NSMakePoint(1, 0.5), NSMakePoint(0, 0.5))];

        // Below the menu bar on purpose: covering it would blind the model in
        // screenshots and make it click menus it cannot see.
        NSView *b = banner(W);
        NSRect bf = b.frame;
        b.frame = NSMakeRect((W - bf.size.width) / 2, H - 30 - bf.size.height, bf.size.width, bf.size.height);
        [v addSubview:b];

        win.contentView = v;
        [win orderFrontRegardless];
        [gWindows addObject:win];
    }
}

@interface Watcher : NSObject
@end
@implementation Watcher
- (void)screensChanged:(NSNotification *)n { (void)n; build(); }
@end

int main(int argc, const char **argv) {
    @autoreleasepool {
        gText = @"This computer is being controlled over MCP";
        for (int i = 1; i < argc; i++) {
            if (!strcmp(argv[i], "--text") && i + 1 < argc)
                gText = [NSString stringWithUTF8String:argv[++i]];
        }
        gWindows = [NSMutableArray array];
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        build();
        Watcher *w = [[Watcher alloc] init];
        [[NSNotificationCenter defaultCenter] addObserver:w
                                                 selector:@selector(screensChanged:)
                                                     name:NSApplicationDidChangeScreenParametersNotification
                                                   object:nil];
        [NSApp run];
    }
    return 0;
}
