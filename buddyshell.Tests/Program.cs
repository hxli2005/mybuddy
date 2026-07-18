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
            ("missing state stays on safe baseline", MissingStateUsesSafeBaseline),
            ("touch is transient and baseline returns", TouchReturnsToBaseline),
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

    private static void BodyStepIsOnlyContract()
    {
        var json = JsonSerializer.Serialize(new BodyStepRequest
        {
            ShownId = "expr-1",
            Presence = new BodyPresence { Present = true, Fullscreen = false },
            Event = new BodyEvent { EventId = "chat-1", Type = "chat", Content = "在吗" },
        });
        Contains(json, "shown_id");
        Contains(json, "presence");
        Contains(json, "event_id");
        var response = JsonSerializer.Serialize(new BodyStepResponse { MindStatus = "unavailable" });
        Contains(response, "mind_status");
        Contains(response, "unavailable");
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

    private static void MissingStateUsesSafeBaseline()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(new BaselineSnapshot(false, "sleep"));
        Equal("idle.default.normal", fixture.Controller.Snapshot.BaselinePlanId);
    }

    private static void TouchReturnsToBaseline()
    {
        using var fixture = new Fixture();
        fixture.Advance(10);
        fixture.Renderer.RaiseTouchStarted(TouchZone.Head, "touch-1");
        Equal("touch.head.normal", fixture.Controller.Snapshot.PlanId);
        fixture.Advance(30);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution);
        Equal("idle.default.normal", fixture.Controller.Snapshot.PlanId);
    }

    private static void ChatCompletesWithoutQueue()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            "chat-1",
            AnimationPriority.Think));
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
                ("sleep.normal", AnimationIntent.Sleep, true, false),
                ("idle.read.normal", AnimationIntent.Read, true, false),
                ("idle.write.normal", AnimationIntent.Write, true, false),
                ("idle.gaze.normal", AnimationIntent.Gaze, true, false),
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
                Phase(root, item.Id, AnimationPhaseKind.Body, item.Baseline || item.Pending),
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
        public event EventHandler<TouchDetectedEventArgs>? TouchStarted;
        public event EventHandler<TouchDetectedEventArgs>? TouchDetected { add { } remove { } }
        public void Render(CompositedFrame frame) { }
        public void RaiseTouchStarted(TouchZone zone, string correlation) =>
            TouchStarted?.Invoke(this, new TouchDetectedEventArgs(zone, correlation));
        public void Dispose() { }
    }
}
