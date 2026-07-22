using BuddyShell;
using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.IO;
using System.Text.Json;
using System.Windows;

namespace BuddyShell.Tests;

internal static class Program
{
    [STAThread]
    private static int Main()
    {
        var tests = new (string Name, Action Run)[]
        {
            ("body step is the only wire contract", BodyStepIsOnlyContract),
            ("edge read cue stays a closed physical fact", EdgeReadCueStaysClosed),
            ("edge reveal stays a closed physical fact", EdgeRevealStaysClosed),
            ("read presentation stays bound to its scheduled surface", ReadPresentationStaysBound),
            ("edge docking exposes only a narrow strip", EdgeDockingExposesNarrowStrip),
            ("edge transitions preserve side-hide continuity", EdgeTransitionsPreserveContinuity),
            ("body action catalog adds same-shape actions as data", BodyActionCatalogIsDataDriven),
            ("read loops until its duration then emits a receipt", ReadLoopsUntilDurationThenEmitsReceipt),
            ("raised drag holds until release and interrupts with a receipt", RaisedDragHoldsUntilRelease),
            ("touch interrupts read without a completed receipt", TouchInterruptsRead),
            ("walk displacement stays inside work area", WalkDisplacementStaysInsideWorkArea),
            ("walk completion emits a physical receipt", WalkCompletionEmitsReceipt),
            ("animation fault never becomes completed life", AnimationFaultNeverCompletesActivity),
            ("chat think completes without a presentation queue", ChatCompletesWithoutQueue),
            ("API key is DPAPI protected", ApiKeyIsProtected),
        };
        var failed = 0;
        foreach (var test in tests)
        {
            try
            {
                test.Run();
                Console.WriteLine($"PASS {test.Name}");
            }
            catch (Exception exception)
            {
                failed += 1;
                Console.Error.WriteLine($"FAIL {test.Name}: {exception}");
            }
        }
        Console.WriteLine($"RESULT total={tests.Length} failed={failed}");
        return failed == 0 ? 0 : 1;
    }

    private static void BodyActionCatalogIsDataDriven()
    {
        Equal(BodyActionShape.Stationary, BodyActionCatalog.Default.Get("read").Shape);
        Equal(BodyActionShape.Horizontal, BodyActionCatalog.Default.Get("walk").Shape);
        Equal(BodyActionShape.Interactive, BodyActionCatalog.Default.Get("raise").Shape);
        Equal(
            AnimationIntent.WalkRight,
            BodyActionCatalog.Default.Get("walk").Animation(BodyActionDirection.Right).Intent);

        var added = BodyActionCatalog.Parse(
            """
            [{
              "type": "raise",
              "shape": "stationary",
              "animations": [{
                "direction": "still",
                "intent": "happy",
                "plan_id": "activity.raise.normal",
                "entry": ["RAISE/A"],
                "body": "RAISE/B",
                "exit": "RAISE/C"
              }]
            }]
            """).Get("raise");
        Equal(BodyActionShape.Stationary, added.Shape);
        Equal("activity.raise.normal", added.Animation(BodyActionDirection.Still).PlanId);

        var rejectedExtraField = false;
        try
        {
            BodyActionCatalog.Parse(
                """[{"type":"read","shape":"stationary","animations":[],"effects":{}}]""");
        }
        catch (JsonException)
        {
            rejectedExtraField = true;
        }
        Equal(true, rejectedExtraField);
    }

    private static void BodyStepIsOnlyContract()
    {
        var json = JsonSerializer.Serialize(new BodyStepRequest
        {
            ShownId = "expr-1",
            ActivityReceipt = new BodyActivityReceipt
            {
                ActivityId = "read-1",
                Status = "completed",
            },
            Presence = new BodyPresence { Present = true, Fullscreen = false, Surface = "edge" },
            Event = new BodyEvent { EventId = "chat-1", Type = "chat", Content = "在吗" },
        });
        Contains(json, "shown_id");
        Contains(json, "presence");
        Contains(json, "\"surface\":\"edge\"");
        Contains(json, "activity_receipt");
        Contains(json, "event_id");
        var response = JsonSerializer.Serialize(new BodyStepResponse
        { MindStatus = "unavailable", ActivityConfirmed = true });
        Contains(response, "mind_status");
        Contains(response, "unavailable");
        Contains(response, "activity_confirmed");
    }

    private static void EdgeReadCueStaysClosed()
    {
        var json = JsonSerializer.Serialize(new BodyStepRequest
        {
            ActivityReceipt = new BodyActivityReceipt
            {
                ActivityId = "edge-read-1",
                Status = "completed",
                Reason = "edge_cue_finished",
            },
            Presence = new BodyPresence { Present = true, Fullscreen = false, Surface = "full" },
        });
        Contains(json, "edge_cue_finished");
        Equal(false, json.Contains("meaning", StringComparison.Ordinal));
        Equal(false, json.Contains("content", StringComparison.Ordinal));
    }

    private static void EdgeRevealStaysClosed()
    {
        var json = JsonSerializer.Serialize(new BodyStepRequest
        {
            Event = new BodyEvent { EventId = "edge-reveal-1", Type = "edge_reveal" },
            Presence = new BodyPresence { Present = true, Fullscreen = false, Surface = "full" },
        });
        Contains(json, "edge_reveal");
        Equal(false, json.Contains("meaning", StringComparison.Ordinal));
        Equal(false, json.Contains("content", StringComparison.Ordinal));
    }

    private static void ReadPresentationStaysBound()
    {
        var edge = new BodyActivity { Id = "read-edge", Type = "read", Presentation = "edge" };
        Contains(JsonSerializer.Serialize(edge), "\"presentation\":\"edge\"");
        Equal(true, edge.MatchesSurface("edge"));
        Equal(false, edge.MatchesSurface("full"));
        var legacy = new BodyActivity { Id = "read-legacy", Type = "read" };
        Equal(true, legacy.MatchesSurface("full"));
        Equal(false, legacy.MatchesSurface("edge"));
        Equal(true, new BodyActivity { Type = "walk" }.MatchesSurface("full"));
    }

    private static void EdgeDockingExposesNarrowStrip()
    {
        var area = new Rect(100, 50, 1200, 800);
        const double width = 336;
        const double height = 420;
        Equal(EdgeSide.Left, EdgeDock.Detect(area, area.Left, width));
        Equal(EdgeSide.Left, EdgeDock.Detect(area, area.Left - width / 2, width));
        Equal(EdgeSide.Right, EdgeDock.Detect(area, area.Right - width, width));
        Equal(EdgeSide.Right, EdgeDock.Detect(area, area.Right - width / 2, width));
        Equal<EdgeSide?>(null, EdgeDock.Detect(area, area.Left + 100, width));

        var ratio = EdgeDock.TopRatio(area, height, 240);
        var left = EdgeDock.Place(
            EdgeSide.Left,
            area,
            width,
            height,
            EdgeDock.TopFromRatio(area, height, ratio));
        var right = EdgeDock.Place(EdgeSide.Right, area, width, height, -1000);
        Equal(EdgeDock.VisibleWidth, left.X + width - area.Left);
        Equal(EdgeDock.VisibleWidth, area.Right - right.X);
        Equal(240.0, left.Y);
        Equal(area.Top, right.Y);
    }

    private static void EdgeTransitionsPreserveContinuity()
    {
        using var fixture = new Fixture();
        fixture.Controller.BeginInteractive(new AnimationRequest(
            AnimationIntent.Raised,
            AnimationSource.DirectManipulation,
            "raise-before-edge"));
        fixture.Advance(10);
        var main = new AnimationRequest(
            AnimationIntent.EdgeLeft,
            AnimationSource.DirectManipulation,
            "edge-main-1");
        fixture.Controller.BeginInteractive(main);
        Equal("edge.left.normal", fixture.Controller.Snapshot.PlanId);
        Equal(AnimationPhaseKind.Entry, fixture.Controller.Snapshot.Phase);
        fixture.Advance(10);

        var rise = new AnimationRequest(
            AnimationIntent.EdgeLeftRise,
            AnimationSource.DirectManipulation,
            "edge-rise-1");
        fixture.Controller.BeginInteractive(rise);
        fixture.Advance(10);
        var resumedMain = main with { CorrelationId = "edge-main-2" };
        fixture.Controller.EndInteractive(rise.CorrelationId, resumedMain, followUpResumeBody: true);
        Equal("edge.left.rise.normal", fixture.Controller.Snapshot.PlanId);
        Equal(AnimationPhaseKind.Exit, fixture.Controller.Snapshot.Phase);
        fixture.Advance(10);
        Equal("edge.left.normal", fixture.Controller.Snapshot.PlanId);
        Equal(AnimationPhaseKind.Body, fixture.Controller.Snapshot.Phase);

        var reveal = main with { CorrelationId = "edge-reveal" };
        fixture.Controller.BeginInteractive(reveal, resumeBody: true);
        Equal(AnimationPhaseKind.Body, fixture.Controller.Snapshot.Phase);
        fixture.Controller.EndInteractive(reveal.CorrelationId);
        Equal(AnimationPhaseKind.Exit, fixture.Controller.Snapshot.Phase);
        fixture.Advance(10);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
        Equal("idle.default.normal", fixture.Controller.Snapshot.PlanId);
    }

    private static void ApiKeyIsProtected()
    {
        var previous = Environment.GetEnvironmentVariable("BUDDYSHELL_DATA_DIR");
        var root = Path.Combine(Path.GetTempPath(), "buddyshell-mini-settings", Guid.NewGuid().ToString("N"));
        Environment.SetEnvironmentVariable("BUDDYSHELL_DATA_DIR", root);
        try
        {
            var settings = new ShellSettings();
            const string secret = "sk-or-test-not-a-real-key";
            SettingsStore.SaveApiKey(settings, secret);
            Equal(secret, SettingsStore.ReadApiKey(settings));
            var disk = File.ReadAllText(SettingsStore.SettingsPath);
            Equal(false, disk.Contains(secret, StringComparison.Ordinal));
        }
        finally
        {
            Environment.SetEnvironmentVariable("BUDDYSHELL_DATA_DIR", previous);
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static void ReadLoopsUntilDurationThenEmitsReceipt()
    {
        using var fixture = new Fixture();
        ActivityFinishedEventArgs? receipt = null;
        fixture.Controller.ActivityFinished += (_, args) => receipt = args;
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Read,
            AnimationSource.State,
            "read-1", 100));
        Equal("activity.read.normal", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(30);
        Equal(null, receipt);
        Equal(AnimationPhaseKind.Body, fixture.Controller.Snapshot.Phase);
        Equal(AnimationExecutionKind.Transient, fixture.Controller.Snapshot.Execution);
        fixture.Advance(90);
        Equal("read-1", receipt?.ActivityId);
        Equal(true, receipt?.Completed);
        Equal("idle.default.normal", fixture.Controller.Snapshot.BaselinePlanId);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
        Equal("completed", receipt?.Status);
        Equal("animation_finished", receipt?.Reason);
    }

    private static void RaisedDragHoldsUntilRelease()
    {
        using var fixture = new Fixture();
        ActivityFinishedEventArgs? receipt = null;
        fixture.Controller.ActivityFinished += (_, args) => receipt = args;
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Read,
            AnimationSource.State,
            "read-before-raise"));
        fixture.Controller.BeginInteractive(new AnimationRequest(
            AnimationIntent.Raised,
            AnimationSource.DirectManipulation,
            "raise-1"));

        Equal("read-before-raise", receipt?.ActivityId);
        Equal("interrupted", receipt?.Status);
        Equal("raise", receipt?.Reason);
        Equal("interaction.raise.normal", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(10);
        Equal(AnimationPhaseKind.Body, fixture.Controller.Snapshot.Phase);
        fixture.Advance(100);
        Equal(AnimationPhaseKind.Body, fixture.Controller.Snapshot.Phase);

        fixture.Controller.EndInteractive("raise-1");
        Equal(AnimationPhaseKind.Exit, fixture.Controller.Snapshot.Phase);
        fixture.Advance(10);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
        Equal("idle.default.normal", fixture.Controller.Snapshot.PlanId);
    }

    private static void TouchInterruptsRead()
    {
        using var fixture = new Fixture();
        ActivityFinishedEventArgs? receipt = null;
        fixture.Controller.ActivityFinished += (_, args) => receipt = args;
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Read,
            AnimationSource.State,
            "read-interrupted"));
        fixture.Renderer.RaiseTouchStarted(TouchZone.Head, "touch-1");
        Equal("read-interrupted", receipt?.ActivityId);
        Equal(false, receipt?.Completed);
        Equal("touch.head.normal", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(30);
        Equal("interrupted", receipt?.Status);
        Equal("touch", receipt?.Reason);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
        Equal("idle.default.normal", fixture.Controller.Snapshot.PlanId);
    }

    private static void WalkDisplacementStaysInsideWorkArea()
    {
        var area = new Rect(0, 0, 800, 600);
        var right = new WalkAttempt("walk-right", 0, 80, 200, 240, area);
        Equal(BodyActionDirection.Right, right.Direction);
        right.Advance(2000);
        Equal(160.0, right.Left);
        Equal(true, right.Contains(right.Left, right.Top));

        var left = new WalkAttempt("walk-left", 560, 80, 200, 240, area);
        Equal(BodyActionDirection.Left, left.Direction);
        left.Advance(10000);
        Equal(0.0, left.Left);
        Equal(true, left.Contains(left.Left, left.Top));
        var json = JsonSerializer.Serialize(left.Capture(left.Left, left.Top));
        Contains(json, "start_left");
        Contains(json, "work_right");
    }

    private static void WalkCompletionEmitsReceipt()
    {
        using var fixture = new Fixture();
        ActivityFinishedEventArgs? receipt = null;
        fixture.Controller.ActivityFinished += (_, args) => receipt = args;
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.WalkRight,
            AnimationSource.State,
            "walk-1"));
        Equal("activity.walk.right.normal", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(30);
        Equal("walk-1", receipt?.ActivityId);
        Equal("completed", receipt?.Status);
        Equal("animation_finished", receipt?.Reason);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
    }

    private static void AnimationFaultNeverCompletesActivity()
    {
        using var fixture = new Fixture();
        ActivityFinishedEventArgs? receipt = null;
        fixture.Controller.ActivityFinished += (_, args) => receipt = args;
        fixture.Renderer.ThrowNextRender = true;
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.WalkLeft,
            AnimationSource.State,
            "walk-fault"));

        Equal("walk-fault", receipt?.ActivityId);
        Equal("failed", receipt?.Status);
        Equal("animation_fault", receipt?.Reason);
        Equal(false, receipt?.Completed);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
    }

    private static void ChatCompletesWithoutQueue()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            "chat-1"));
        fixture.Advance(10);
        Equal(true, fixture.Controller.Snapshot.ThinkPending);
        fixture.Controller.Complete("chat-1", new AnimationOutcome(AnimationIntent.Happy));
        Equal(false, fixture.Controller.Snapshot.ThinkPending);
        Equal("speech.happy", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(30);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
    }

    private static void Contains(string value, string expected)
    {
        if (!value.Contains(expected, StringComparison.Ordinal))
            throw new InvalidOperationException($"missing {expected}: {value}");
    }

    private static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
            throw new InvalidOperationException($"expected={expected} actual={actual}");
    }

    private sealed class Fixture : IDisposable
    {
        private readonly string _root;

        public Fixture()
        {
            _root = Path.Combine(Path.GetTempPath(), "buddyshell-mini-tests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(_root);
            Clock = new ManualAnimationClock();
            Renderer = new RecordingRenderer();
            Controller = new AnimationController(BuildManifest(_root), Renderer, Clock, autoStart: false);
        }

        public ManualAnimationClock Clock { get; }
        public RecordingRenderer Renderer { get; }
        public AnimationController Controller { get; }

        public void Advance(int milliseconds)
        {
            for (var elapsed = 0; elapsed < milliseconds; elapsed += 10)
            {
                Clock.Advance(10);
                Controller.Tick();
            }
        }

        public void Dispose()
        {
            Controller.Dispose();
            Directory.Delete(_root, recursive: true);
        }

        private static AnimationManifest BuildManifest(string root)
        {
            var definitions = new (string Id, AnimationIntent Intent, bool Baseline, bool Pending)[]
            {
                ("idle.default.normal", AnimationIntent.Idle, true, false),
                ("activity.read.normal", AnimationIntent.Read, false, false),
                ("activity.walk.left.normal", AnimationIntent.WalkLeft, false, false),
                ("activity.walk.right.normal", AnimationIntent.WalkRight, false, false),
                ("interaction.raise.normal", AnimationIntent.Raised, false, false),
                ("edge.left.normal", AnimationIntent.EdgeLeft, false, false),
                ("edge.right.normal", AnimationIntent.EdgeRight, false, false),
                ("edge.left.rise.normal", AnimationIntent.EdgeLeftRise, false, false),
                ("edge.right.rise.normal", AnimationIntent.EdgeRightRise, false, false),
                ("think.normal", AnimationIntent.Think, false, true),
                ("touch.head.normal", AnimationIntent.TouchHeadReflex, false, false),
                ("touch.body.happy", AnimationIntent.TouchBodyReflex, false, false),
                ("speech.neutral", AnimationIntent.Neutral, false, false),
                ("speech.happy", AnimationIntent.Happy, false, false),
            };
            var plans = definitions.Select(item => new AnimationPlan(
                item.Id,
                item.Intent,
                Phase(root, item.Id, AnimationPhaseKind.Entry),
                Phase(root, item.Id, AnimationPhaseKind.Body,
                    item.Baseline || item.Pending || item.Intent is AnimationIntent.Read or
                        AnimationIntent.Raised or AnimationIntent.EdgeLeft or AnimationIntent.EdgeRight or
                        AnimationIntent.EdgeLeftRise or AnimationIntent.EdgeRightRise),
                Phase(root, item.Id, AnimationPhaseKind.Exit),
                item.Baseline,
                item.Pending));
            return AnimationManifest.Create(root, plans, validate: false);
        }

        private static AnimationPhasePlan Phase(
            string root,
            string id,
            AnimationPhaseKind kind,
            bool loop = false)
        {
            var path = Path.Combine(root, $"{id}-{kind}.png");
            File.WriteAllBytes(path, []);
            return new AnimationPhasePlan(
                kind,
                loop,
                [new AnimationLayerPlan("main", 0, [new AnimationFrameSpec(path, 10)])]);
        }
    }

    private sealed class RecordingRenderer : IAnimationRenderer
    {
        public UIElement View { get; } = new();
        public bool ThrowNextRender { get; set; }
        public event EventHandler<TouchDetectedEventArgs>? TouchStarted;
        public event EventHandler<TouchDetectedEventArgs>? TouchDetected { add { } remove { } }
        public void Render(CompositedFrame frame)
        {
            if (!ThrowNextRender) return;
            ThrowNextRender = false;
            throw new InvalidOperationException("render failed");
        }
        public void RaiseTouchStarted(TouchZone zone, string correlation) =>
            TouchStarted?.Invoke(this, new TouchDetectedEventArgs(zone, correlation));
        public void Dispose() { }
    }
}
