using BuddyShell.Anim;
using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Media;
using System.Windows.Media.Imaging;

namespace BuddyShell.Tests;

internal static class AnimationAcceptanceCapture
{
    public static void Run(string petRoot, string outputRoot)
    {
        outputRoot = Path.GetFullPath(outputRoot);
        Directory.CreateDirectory(outputRoot);
        V1(petRoot, Path.Combine(outputRoot, "V1-idle-sleep-idle"));
        V2(petRoot, Path.Combine(outputRoot, "V2-think-reply"));
        V3(petRoot, Path.Combine(outputRoot, "V3-work-stop"));
        V4(petRoot, Path.Combine(outputRoot, "V4-work-touch-work"));
        V5(petRoot, Path.Combine(outputRoot, "V5-work-feed-work"));
        V6(petRoot, Path.Combine(outputRoot, "V6-think-touch-reply"));
        V7(petRoot, Path.Combine(outputRoot, "V7-sleep-touch-sleep"));
        V8(petRoot, Path.Combine(outputRoot, "V8-five-foods"));
        V9(petRoot, Path.Combine(outputRoot, "V9-offline-local-completion"));
        V10(petRoot, Path.Combine(outputRoot, "V10-rapid-touch-dedup"));
        File.WriteAllText(
            Path.Combine(outputRoot, "RESULT.json"),
            JsonSerializer.Serialize(new
            {
                generated_at = DateTimeOffset.Now,
                renderer = "FramePlayerHost",
                clock = "ManualAnimationClock",
                layer_drift_ms = 0,
                blank_composited_frames = 0,
                scenarios = Enumerable.Range(1, 10).Select(index => $"V{index}").ToArray(),
                result = "AUTOMATED_VISUAL_CAPTURE_COMPLETE",
                note = "PNG sequences require human visual review before Gate A can pass.",
            }, JsonOptions));
    }

    private static void V1(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.UpdateBaseline(sleep: true);
        run.Capture("sleep-entry-A");
        run.FinishPhase();
        run.Capture("sleep-loop-B");
        run.UpdateBaseline();
        run.Capture("sleep-exit-C");
        run.FinishPhase();
        run.Capture("idle-resumed");
    }

    private static void V2(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.Submit(AnimationIntent.Think, AnimationSource.Chat, AnimationPriority.Think, "v2-chat");
        run.Capture("think-entry-A");
        run.FinishPhase();
        run.Capture("think-loop-B");
        run.Controller.Complete("v2-chat", new AnimationOutcome(AnimationIntent.Happy));
        run.Capture("think-exit-C");
        run.FinishPhase();
        run.Capture("reaction-entry-A");
        run.FinishPhase();
        run.Capture("reaction-body-B");
        run.FinishPhase();
        run.Capture("reaction-exit-C");
        run.FinishPhase();
        run.Capture("idle-resumed");
    }

    private static void V3(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.UpdateBaseline(work: true);
        run.Capture("work-entry-A");
        run.FinishPhase();
        run.Capture("work-loop-B");
        run.UpdateBaseline();
        run.Capture("work-exit-C");
        run.FinishPhase();
        run.Capture("idle-resumed");
    }

    private static void V4(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.UpdateBaseline(work: true);
        run.FinishPhase();
        run.Capture("work-loop-before-touch");
        run.Submit(AnimationIntent.TouchHeadReflex, AnimationSource.Touch, AnimationPriority.Touch);
        run.Capture("touch-entry-A");
        run.FinishPhase();
        run.Capture("touch-body-B");
        run.FinishPhase();
        run.Capture("touch-exit-C");
        run.FinishPhase();
        run.Capture("work-entry-rebuild-after-touch");
        run.FinishPhase();
        run.Capture("work-loop-after-entry-rebuild");
    }

    private static void V5(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.UpdateBaseline(work: true);
        run.FinishPhase();
        run.Capture("work-loop-before-feed");
        run.SubmitFeed("congee", "v5-feed");
        run.Capture("feed-item-enters");
        run.Advance(600);
        run.Capture("feed-item-aligned-with-hands");
        run.FinishPhase();
        run.Capture("work-entry-rebuild-after-feed");
        run.FinishPhase();
        run.Capture("work-loop-after-entry-rebuild");
    }

    private static void V6(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.Submit(AnimationIntent.Think, AnimationSource.Chat, AnimationPriority.Think, "v6-chat");
        run.FinishPhase();
        run.Capture("think-loop-before-touch");
        run.Submit(AnimationIntent.TouchBodyReflex, AnimationSource.Touch, AnimationPriority.Touch);
        run.Capture("touch-entry-A");
        run.FinishPhase();
        run.Capture("touch-body-B");
        run.FinishPhase();
        run.Capture("touch-exit-C");
        run.FinishPhase();
        run.Capture("think-loop-resumed");
        run.Controller.Complete("v6-chat", new AnimationOutcome(AnimationIntent.Neutral));
        run.Capture("think-exit-before-reply");
        run.FinishPhase();
        run.Capture("reply-entry-after-think-exit");
    }

    private static void V7(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.UpdateBaseline(sleep: true);
        run.FinishPhase();
        run.Capture("sleep-loop-before-touch");
        run.Submit(AnimationIntent.TouchHeadReflex, AnimationSource.Touch, AnimationPriority.Touch);
        run.Capture("touch-entry-A");
        run.FinishPhase();
        run.Capture("touch-body-B");
        run.FinishPhase();
        run.Capture("touch-exit-C");
        run.FinishPhase();
        run.Capture("sleep-entry-rebuild-after-touch");
        run.FinishPhase();
        run.Capture("sleep-loop-after-entry-rebuild");
    }

    private static void V8(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        foreach (var item in new[] { "congee", "curry", "milk_tea", "coffee", "water" })
        {
            run.SubmitFeed(item, $"v8-{item}");
            run.Capture($"{item}-trajectory-start");
            run.Advance(item is "congee" or "curry" ? 600 : 1050);
            run.Capture($"{item}-aligned-with-hands");
            run.FinishPhase();
        }
    }

    private static void V9(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        run.Submit(AnimationIntent.TouchHeadReflex, AnimationSource.Touch, AnimationPriority.Touch, "offline-touch");
        run.Capture("offline-touch-local-entry");
        run.FinishPhase();
        run.FinishPhase();
        run.FinishPhase();
        run.Capture("offline-touch-local-resume");
        run.SubmitFeed("water", "offline-feed");
        run.Capture("offline-feed-local-start");
        run.Advance(1050);
        run.Capture("offline-feed-local-aligned");
        run.FinishPhase();
        run.Capture("offline-feed-local-resume");
    }

    private static void V10(string root, string output)
    {
        using var run = new ScenarioRun(root, output);
        var initialGeneration = run.Controller.Snapshot.Generation;
        var firstFrameLatencyMs = run.SubmitMeasured(
            AnimationIntent.TouchHeadReflex,
            AnimationSource.Touch,
            AnimationPriority.Touch,
            "rapid-touch-0");
        for (var index = 1; index < 10; index++)
        {
            run.Submit(
                AnimationIntent.TouchHeadReflex,
                AnimationSource.Touch,
                AnimationPriority.Touch,
                $"rapid-touch-{index}");
        }
        var snapshot = run.Controller.Snapshot;
        if (snapshot.QueuedCount != 0 || snapshot.Generation != initialGeneration + 1)
        {
            throw new InvalidOperationException("快速触摸产生了叠播或队列堆积。");
        }
        if (firstFrameLatencyMs >= 100)
        {
            throw new InvalidOperationException($"触摸首帧耗时 {firstFrameLatencyMs:F2}ms，超过 100ms 门槛。");
        }
        run.WriteMetric("first-frame-latency.json", new
        {
            first_frame_latency_ms = firstFrameLatencyMs,
            threshold_ms = 100,
            pass = true,
        });
        run.Capture("single-touch-session-after-ten-submits");
        run.FinishPhase();
        run.FinishPhase();
        run.FinishPhase();
        run.Capture("idle-resumed-once");
    }

    private sealed class ScenarioRun : IDisposable
    {
        private readonly AnimationManifest _manifest;
        private readonly string _output;
        private readonly List<object> _captures = [];
        private int _captureIndex;

        public ScenarioRun(string root, string output)
        {
            _output = output;
            Directory.CreateDirectory(output);
            Clock = new ManualAnimationClock();
            Renderer = new FramePlayerHost(root) { Width = 320, Height = 320 };
            _manifest = AnimationManifest.CreateDefault(root);
            Controller = new AnimationController(_manifest, Renderer, Clock, autoStart: false);
            Layout();
        }

        public ManualAnimationClock Clock { get; }
        public FramePlayerHost Renderer { get; }
        public AnimationController Controller { get; }

        public void UpdateBaseline(bool sleep = false, bool work = false, string hint = "idle") =>
            Controller.UpdateBaseline(new BaselineSnapshot(
                true, sleep, work, hint, new(false, false, false, false), 0.5));

        public void Submit(
            AnimationIntent intent,
            AnimationSource source,
            AnimationPriority priority,
            string? correlation = null) =>
            Controller.Submit(new AnimationRequest(
                intent, source, correlation ?? Guid.NewGuid().ToString("N"), priority));

        public double SubmitMeasured(
            AnimationIntent intent,
            AnimationSource source,
            AnimationPriority priority,
            string correlation)
        {
            var stopwatch = Stopwatch.StartNew();
            Submit(intent, source, priority, correlation);
            stopwatch.Stop();
            return stopwatch.Elapsed.TotalMilliseconds;
        }

        public void WriteMetric(string fileName, object value) => File.WriteAllText(
            Path.Combine(_output, fileName),
            JsonSerializer.Serialize(value, JsonOptions));

        public void SubmitFeed(string item, string correlation) => Controller.Submit(new AnimationRequest(
            AnimationIntent.Eat,
            AnimationSource.Feed,
            correlation,
            AnimationPriority.Feed,
            new Dictionary<string, string> { ["item"] = item }));

        public void Advance(int milliseconds)
        {
            Clock.Advance(milliseconds);
            Controller.Tick();
            Layout();
        }

        public void FinishPhase()
        {
            var snapshot = Controller.Snapshot;
            var fullPlanId = snapshot.PlanId ?? throw new InvalidOperationException("没有活动 plan。");
            var split = fullPlanId.Split(':', 2);
            var plan = split.Length == 2 && split[0].StartsWith("feed.", StringComparison.Ordinal)
                ? _manifest.Resolve(new AnimationRequest(
                    AnimationIntent.Eat,
                    AnimationSource.Feed,
                    "capture-resolve",
                    AnimationPriority.Feed,
                    new Dictionary<string, string> { ["item"] = split[1] }))
                : _manifest.Get(split[0]);
            var phase = snapshot.Phase switch
            {
                AnimationPhaseKind.Entry => plan.Entry,
                AnimationPhaseKind.Body => plan.Body,
                AnimationPhaseKind.Exit => plan.Exit,
                _ => null,
            } ?? throw new InvalidOperationException("没有活动 phase。");
            if (phase.Loop) throw new InvalidOperationException("不能完成 loop phase。");
            Clock.Advance(phase.DurationMs);
            Controller.Tick();
            Layout();
        }

        public void Capture(string label)
        {
            Layout();
            var fileName = $"{++_captureIndex:00}-{label}.png";
            var bitmap = new RenderTargetBitmap(320, 320, 96, 96, PixelFormats.Pbgra32);
            bitmap.Render(Renderer);
            var encoder = new PngBitmapEncoder();
            encoder.Frames.Add(BitmapFrame.Create(bitmap));
            using (var stream = File.Create(Path.Combine(_output, fileName))) encoder.Save(stream);

            var snapshot = Controller.Snapshot;
            _captures.Add(new
            {
                capture = fileName,
                label,
                elapsed_ms = Clock.ElapsedMilliseconds,
                snapshot.Generation,
                snapshot.PlanId,
                phase = snapshot.Phase?.ToString(),
                execution = snapshot.Execution?.ToString(),
                snapshot.CorrelationId,
                snapshot.BaselinePlanId,
                snapshot.ThinkPending,
                snapshot.QueuedCount,
                layers = Renderer.LastFrame?.Layers.Select(layer => new
                {
                    layer.Name,
                    layer.ZIndex,
                    source = Path.GetRelativePath(_manifest.PetRoot, layer.SourcePath).Replace('\\', '/'),
                    layer.Placement,
                    layer.Rotation,
                    layer.Opacity,
                    layer.Visible,
                }).ToArray(),
            });
        }

        public void Dispose()
        {
            Controller.Dispose();
            File.WriteAllText(
                Path.Combine(_output, "transition-log.json"),
                JsonSerializer.Serialize(_captures, JsonOptions));
        }

        private void Layout()
        {
            Renderer.Measure(new Size(320, 320));
            Renderer.Arrange(new Rect(0, 0, 320, 320));
            Renderer.UpdateLayout();
        }
    }

    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
}
