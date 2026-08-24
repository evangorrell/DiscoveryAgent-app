#import <Cocoa/Cocoa.h>
#import <unistd.h>

static const NSTimeInterval ReadinessTimeout = 2.0;

static BOOL WriteResult(NSString *path, NSDictionary *payload) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:payload options:0 error:&error];
    return data != nil && [data writeToFile:path options:NSDataWritingAtomic error:&error];
}

static void AdvanceMainRunLoop(void) {
    [NSRunLoop.currentRunLoop runMode:NSDefaultRunLoopMode
                           beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.01]];
}

static BOOL ApplicationReady(void) {
    NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
    return NSApp.isActive && frontmost.processIdentifier == getpid();
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

static BOOL WaitForStableApplication(void) {
    NSUInteger stableTurns = 0;
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:ReadinessTimeout];
    while (deadline.timeIntervalSinceNow > 0 && stableTurns < 3) {
        AdvanceMainRunLoop();
        stableTurns = ApplicationReady() ? stableTurns + 1 : 0;
    }
    return stableTurns >= 3;
}

static BOOL WaitForStablePanel(NSOpenPanel *panel) {
    NSUInteger stableTurns = 0;
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:ReadinessTimeout];
    while (deadline.timeIntervalSinceNow > 0 && stableTurns < 5) {
        AdvanceMainRunLoop();
        stableTurns = PanelReady(panel) ? stableTurns + 1 : 0;
    }
    if (stableTurns < 5) return NO;

    // Require another completed event-loop turn after the final presentation
    // transition. Internal activation flags alone are not interaction readiness.
    AdvanceMainRunLoop();
    return PanelReady(panel);
}

static int Fail(NSString *resultPath, NSString *code, NSString *message, NSOpenPanel *panel) {
    if (panel != nil) [panel orderOut:nil];
    WriteResult(resultPath, @{
        @"status": @"error",
        @"code": code,
        @"message": message,
    });
    return 1;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) return 64;
        NSString *resultPath = [NSString stringWithUTF8String:argv[1]];
        if (resultPath.length == 0) return 64;
        WriteResult(resultPath, @{@"status": @"running", @"pid": @(getpid())});

        @try {
            [NSApplication sharedApplication];
            [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
            [NSApp finishLaunching];

            NSOpenPanel *panel = [NSOpenPanel openPanel];
            panel.canChooseDirectories = YES;
            panel.canChooseFiles = NO;
            panel.allowsMultipleSelection = YES;
            panel.canCreateDirectories = NO;
            panel.prompt = @"Select";
            panel.ignoresMouseEvents = NO;
            panel.alphaValue = 1.0;

            // LaunchServices gives this bundled executable a real application
            // identity, so it can become frontmost before exposing any window.
            [NSApp activateIgnoringOtherApps:YES];
            if (!WaitForStableApplication()) {
                return Fail(
                    resultPath,
                    @"picker_not_ready",
                    @"The folder chooser could not become the frontmost application.",
                    nil
                );
            }

            [panel makeKeyAndOrderFront:nil];
            if (!WaitForStablePanel(panel)) {
                return Fail(
                    resultPath,
                    @"picker_not_ready",
                    @"The folder chooser could not become ready for input.",
                    panel
                );
            }

            NSModalResponse response = [panel runModal];
            NSDictionary *payload = nil;
            if (response == NSModalResponseOK) {
                NSMutableArray<NSString *> *paths = [NSMutableArray array];
                for (NSURL *url in panel.URLs) {
                    if (url.path != nil) [paths addObject:url.path];
                }
                payload = @{@"status": @"selected", @"paths": paths};
            } else {
                payload = @{@"status": @"cancelled", @"paths": @[]};
            }
            [panel orderOut:nil];
            if (!WriteResult(resultPath, payload)) return 74;
            return 0;
        } @catch (NSException *exception) {
            return Fail(
                resultPath,
                @"picker_failed",
                @"The folder chooser encountered an internal native error.",
                nil
            );
        }
    }
}
