#import <Cocoa/Cocoa.h>
#import <unistd.h>

static FILE *Output = NULL;

static void Advance(void) {
    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
}

static BOOL ApplicationReady(void) {
    return NSApp.isActive
        && NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier == getpid();
}

static BOOL PanelReady(NSOpenPanel *panel) {
    return ApplicationReady()
        && panel.isVisible
        && panel.isKeyWindow
        && panel.isMainWindow
        && panel.firstResponder != nil
        && !panel.ignoresMouseEvents
        && panel.alphaValue >= 0.99;
}

static void Record(NSString *phase, NSOpenPanel *panel) {
    NSRunningApplication *owner = NSRunningApplication.currentApplication;
    NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
    NSString *responder = panel.firstResponder
        ? NSStringFromClass(panel.firstResponder.class)
        : @"";
    NSDictionary *state = @{
        @"phase": phase,
        @"appActive": @(NSApp.isActive),
        @"ownerActive": @(owner.isActive),
        @"frontmostMatches": @(frontmost.processIdentifier == getpid()),
        @"visible": @(panel.isVisible),
        @"key": @(panel.isKeyWindow),
        @"main": @(panel.isMainWindow),
        @"ignoresMouse": @(panel.ignoresMouseEvents),
        @"alpha": @(panel.alphaValue),
        @"responder": responder,
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:state options:0 error:nil];
    fwrite(data.bytes, 1, data.length, Output);
    fputc('\n', Output);
    fflush(Output);
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2 || (Output = fopen(argv[1], "w")) == NULL) return 64;
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
        [NSApp finishLaunching];

        NSOpenPanel *panel = [NSOpenPanel openPanel];
        panel.canChooseDirectories = YES;
        panel.canChooseFiles = NO;
        panel.allowsMultipleSelection = YES;
        panel.ignoresMouseEvents = NO;
        panel.alphaValue = 1.0;

        [NSApp activateIgnoringOtherApps:YES];
        NSUInteger stable = 0;
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2.0];
        while (deadline.timeIntervalSinceNow > 0 && stable < 3) {
            Advance();
            Record(@"activation", panel);
            stable = ApplicationReady() ? stable + 1 : 0;
        }
        if (stable < 3) return 2;

        [panel makeKeyAndOrderFront:nil];
        stable = 0;
        deadline = [NSDate dateWithTimeIntervalSinceNow:2.0];
        while (deadline.timeIntervalSinceNow > 0 && stable < 5) {
            Advance();
            Record(@"presentation", panel);
            stable = PanelReady(panel) ? stable + 1 : 0;
        }
        if (stable < 5) return 3;

        for (NSUInteger turn = 0; turn < 4; turn += 1) {
            Advance();
            Record(@"stable", panel);
            if (!PanelReady(panel)) return 4;
        }

        NSTimer *timer = [NSTimer timerWithTimeInterval:0.2 repeats:NO block:^(NSTimer *unused) {
            Record(@"modal", panel);
            [panel cancel:nil];
        }];
        [NSRunLoop.currentRunLoop addTimer:timer forMode:NSModalPanelRunLoopMode];
        [panel runModal];
        [panel orderOut:nil];
        fclose(Output);
        return 0;
    }
}
