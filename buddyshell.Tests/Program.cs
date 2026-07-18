using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;

namespace BuddyShell.Tests;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        var realSoakIndex = Array.IndexOf(args, "--real-soak-seconds");
        if (realSoakIndex >= 0)
        {
            if (realSoakIndex + 1 >= args.Length ||
                !int.TryParse(args[realSoakIndex + 1], out var durationSeconds) ||
                durationSeconds <= 0)
            {
                throw new ArgumentException("--real-soak-seconds 需要正整数秒数。");
            }
            var outputIndex = Array.IndexOf(args, "--soak-output");
            if (outputIndex < 0 || outputIndex + 1 >= args.Length)
            {
                throw new ArgumentException("实时 soak 需要 --soak-output 输出路径。");
            }
            return AnimationRealSoak.Run(AssetLocator.FindPetRoot(), args[outputIndex + 1], durationSeconds);
        }

        var tests = new List<(string Name, Action Run)>
        {
            ("timeline keeps layered feed synchronized", TimelineKeepsLayersSynchronized),
            ("timeline advances per-frame transforms", TimelineAdvancesTransforms),
            ("sleep baseline uses entry body exit", SleepUsesEntryBodyExit),
            ("work rebuilds entry after transient", WorkRebuildsEntryAfterTransient),
            ("all phased baselines rebuild entry after transient", PhasedBaselinesRebuildEntryAfterTransient),
            ("think completion is correlation based", ThinkCompletionIsCorrelationBased),
            ("touch preempts and pending think resumes", TouchPreemptsPendingThink),
            ("feed is three layers and duplicate is dropped", FeedIsLayeredAndDeduplicated),
            ("same baseline updates do not restart", SameBaselineDoesNotRestart),
            ("empty bridge action adds no animation", EmptyActionAddsNoAnimation),
            ("body step carries chat and shown receipt", BodyStepCarriesChatAndShownReceipt),
            ("renderer fault keeps previous frame and retries", RendererFaultRetries),
            ("chat error exits think and resumes", ChatErrorResumesBaseline),
            ("feed lock ignores touch visuals", FeedLockIgnoresTouch),
            ("baseline transitions queue network reactions", BaselineTransitionQueuesReaction),
            ("disconnect uses safe baseline and reconnect restores mind baseline", DisconnectUsesSafeBaseline),
            ("stretch hint fires once as flourish", StretchHintFiresOnce),
            ("30 minute virtual randomized soak", VirtualRandomizedSoak),
            ("non chat think cannot create permanent pending", NonChatThinkIsDropped),
            ("thinking reaction cannot restart pending", ThinkingReactionDoesNotRestartPending),
            ("queued think completion removes stale request", QueuedThinkCompletionRemovesRequest),
            ("renderer fault freezes phase time", RendererFaultFreezesTimeline),
            ("touch correlation survives visual and commit events", TouchCorrelationSurvives),
        };
        if (args.Contains("--assets", StringComparer.Ordinal))
        {
            tests.Add(("installed VPet manifest is complete", InstalledManifestIsComplete));
            tests.Add(("decoded frame cache stays within memory budget", InstalledFrameCacheIsBounded));
        }
        var captureIndex = Array.IndexOf(args, "--capture");
        if (captureIndex >= 0)
        {
            if (captureIndex + 1 >= args.Length) throw new ArgumentException("--capture 需要输出目录。");
            tests.Add(("V1-V10 visual sequences captured", () => AnimationAcceptanceCapture.Run(
                AssetLocator.FindPetRoot(),
                args[captureIndex + 1])));
        }

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
                Console.Error.WriteLine($"FAIL {test.Name}: {exception.Message}");
            }
        }
        Console.WriteLine($"RESULT total={tests.Count} failed={failed}");
        return failed == 0 ? 0 : 1;
    }

    private static void InstalledManifestIsComplete()
    {
        var manifest = AnimationManifest.CreateDefault(AssetLocator.FindPetRoot());
        Equal(16, manifest.Plans.Count);
        foreach (var item in new[] { "congee", "curry", "milk_tea", "coffee", "water" })
        {
            var plan = manifest.Resolve(new AnimationRequest(
                AnimationIntent.Eat,
                AnimationSource.Feed,
                item,
                AnimationPriority.Feed,
                new Dictionary<string, string> { ["item"] = item }));
            Equal(3, plan.Body.Layers.Count);
            Equal(plan.Body.Layers[0].DurationMs, plan.Body.Layers[2].DurationMs);
            Equal(item is "congee" or "curry" ? 2675 : 2750, plan.Body.Layers[1].DurationMs);
            if (plan.Body.Layers[1].Frames
                .Where(frame => frame.Visible)
                .Any(frame => frame.Placement?.CoordinateSpace != LayerCoordinateSpace.LogicalCanvas))
            {
                throw new InvalidOperationException($"{item} 存在非 500 逻辑画布轨迹。");
            }
        }
    }

    private static void BodyStepCarriesChatAndShownReceipt()
    {
        var request = new BodyStepRequest
        {
            ShownId = "expr-previous",
            Presence = new BodyPresence { Present = true, Fullscreen = false },
            Event = new BodyEvent
            {
                EventId = "chat-001",
                Content = "今天终于忙完了。",
            },
        };
        using var document = JsonDocument.Parse(JsonSerializer.Serialize(request));
        var root = document.RootElement;
        Equal("expr-previous", root.GetProperty("shown_id").GetString());
        var presence = root.GetProperty("presence");
        Equal(true, presence.GetProperty("present").GetBoolean());
        Equal(false, presence.GetProperty("fullscreen").GetBoolean());
        var bodyEvent = root.GetProperty("event");
        Equal("chat-001", bodyEvent.GetProperty("event_id").GetString());
        Equal("chat", bodyEvent.GetProperty("type").GetString());
        Equal("今天终于忙完了。", bodyEvent.GetProperty("content").GetString());
        Equal(false, root.TryGetProperty("message", out _));

        var touch = new BodyStepRequest
        {
            Event = new BodyEvent { EventId = "touch-001", Type = "touch_head" },
        };
        using var touchDocument = JsonDocument.Parse(JsonSerializer.Serialize(touch));
        var touchEvent = touchDocument.RootElement.GetProperty("event");
        Equal("touch-001", touchEvent.GetProperty("event_id").GetString());
        Equal("touch_head", touchEvent.GetProperty("type").GetString());
        Equal(false, touchEvent.TryGetProperty("content", out _));
    }

    private static void InstalledFrameCacheIsBounded()
    {
        var manifest = AnimationManifest.CreateDefault(AssetLocator.FindPetRoot());
        var cache = new FrameCache();
        foreach (var frame in manifest.Plans.Values
            .SelectMany(plan => new[] { plan.Entry, plan.Body, plan.Exit })
            .OfType<AnimationPhasePlan>()
            .SelectMany(phase => phase.Layers)
            .SelectMany(layer => layer.Frames))
        {
            cache.Get(frame.Path);
        }
        if (cache.Count > 64) throw new InvalidOperationException($"cache entries={cache.Count}");
        if (cache.EstimatedBytes > 64L * 1024 * 1024)
        {
            throw new InvalidOperationException($"cache bytes={cache.EstimatedBytes}");
        }
        cache.Clear();
        Equal(0, cache.Count);
        Equal(0L, cache.EstimatedBytes);
    }

    private static void TimelineKeepsLayersSynchronized()
    {
        var phase = Phase(AnimationPhaseKind.Body, false,
            Layer("back", 0, ("back-1", 100), ("back-2", 100)),
            Layer("front", 2, ("front-1", 50), ("front-2", 150)));
        var first = AnimationTimeline.Compose(1, "feed", phase, 75);
        Equal("back-1", first.Layers[0].SourcePath);
        Equal("front-2", first.Layers[1].SourcePath);
        var second = AnimationTimeline.Compose(1, "feed", phase, 125);
        Equal("back-2", second.Layers[0].SourcePath);
        Equal("front-2", second.Layers[1].SourcePath);
    }

    private static void TimelineAdvancesTransforms()
    {
        var firstPlacement = new LayerPlacement(60, 60, 205, 23, LayerCoordinateSpace.LogicalCanvas);
        var secondPlacement = new LayerPlacement(65, 65, 212, 196, LayerCoordinateSpace.LogicalCanvas);
        var phase = Phase(
            AnimationPhaseKind.Body,
            false,
            new AnimationLayerPlan(
                "item",
                1,
                [
                    new AnimationFrameSpec("same.png", 100, firstPlacement, 25, 0.5),
                    new AnimationFrameSpec("same.png", 100, secondPlacement),
                    new AnimationFrameSpec("same.png", 100, Visible: false),
                ]));
        var first = AnimationTimeline.Compose(1, "feed", phase, 50).Layers.Single();
        var second = AnimationTimeline.Compose(1, "feed", phase, 150).Layers.Single();
        var hidden = AnimationTimeline.Compose(1, "feed", phase, 250).Layers.Single();
        Equal(firstPlacement, first.Placement);
        Equal(25d, first.Rotation);
        Equal(0.5d, first.Opacity);
        Equal(secondPlacement, second.Placement);
        Equal(false, hidden.Visible);
        if (AnimationTimeline.Compose(1, "feed", phase, 50).Signature ==
            AnimationTimeline.Compose(1, "feed", phase, 150).Signature)
        {
            throw new InvalidOperationException("相同图片的 transform 变化没有改变合成签名。");
        }
    }

    private static void SleepUsesEntryBodyExit()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(Baseline(sleeping: true));
        Equal(("sleep.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("sleep.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Controller.UpdateBaseline(Baseline());
        Equal(("sleep.normal", AnimationPhaseKind.Exit), fixture.Current);
        fixture.Advance(10);
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void WorkRebuildsEntryAfterTransient()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Controller.Submit(Request(AnimationIntent.Stretch, AnimationPriority.Response));
        Equal(("idle.stretch.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void PhasedBaselinesRebuildEntryAfterTransient()
    {
        var cases = new (BaselineSnapshot Baseline, string PlanId)[]
        {
            (Baseline(sleeping: true), "sleep.normal"),
            (Baseline(work: true), "work.write.normal"),
            (Baseline() with { IdleHint = "read" }, "idle.read.normal"),
            (Baseline() with { IdleHint = "write" }, "idle.write.normal"),
            (Baseline() with { IdleHint = "gaze" }, "idle.gaze.normal"),
        };

        foreach (var item in cases)
        {
            using var fixture = new Fixture();
            fixture.Controller.UpdateBaseline(item.Baseline);
            fixture.Advance(10);
            Equal((item.PlanId, AnimationPhaseKind.Body), fixture.Current);
            fixture.Controller.Submit(Request(AnimationIntent.TouchHeadReflex, AnimationPriority.Touch));
            fixture.Advance(10);
            fixture.Advance(10);
            fixture.Advance(10);
            Equal((item.PlanId, AnimationPhaseKind.Entry), fixture.Current);
            fixture.Advance(10);
            Equal((item.PlanId, AnimationPhaseKind.Body), fixture.Current);
        }
    }

    private static void ThinkCompletionIsCorrelationBased()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(Request(AnimationIntent.Think, AnimationPriority.Think, "chat-1"));
        fixture.Advance(10);
        Equal(("think.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Controller.Complete("stale-chat", new AnimationOutcome(AnimationIntent.Happy));
        Equal(("think.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Controller.Complete("chat-1", new AnimationOutcome(AnimationIntent.Happy));
        Equal(("think.normal", AnimationPhaseKind.Exit), fixture.Current);
        fixture.Advance(10);
        Equal(("speech.happy", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void TouchPreemptsPendingThink()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(Request(AnimationIntent.Think, AnimationPriority.Think, "chat-2"));
        fixture.Advance(10);
        fixture.Controller.Submit(Request(AnimationIntent.TouchHeadReflex, AnimationPriority.Touch));
        Equal(("touch.head.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("touch.head.normal", AnimationPhaseKind.Body), fixture.Current);
        fixture.Advance(10);
        Equal(("touch.head.normal", AnimationPhaseKind.Exit), fixture.Current);
        fixture.Advance(10);
        Equal(("think.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void FeedIsLayeredAndDeduplicated()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Eat,
            AnimationSource.Feed,
            "feed-1",
            AnimationPriority.Feed,
            new Dictionary<string, string> { ["item"] = "congee" }));
        Equal(3, fixture.Renderer.Frames[^1].Layers.Count);
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Eat,
            AnimationSource.Feed,
            "feed-2",
            AnimationPriority.Feed,
            new Dictionary<string, string> { ["item"] = "curry" }));
        Equal(0, fixture.Controller.Snapshot.QueuedCount);
        fixture.Advance(10);
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void SameBaselineDoesNotRestart()
    {
        using var fixture = new Fixture();
        var generation = fixture.Controller.Snapshot.Generation;
        for (var index = 0; index < 100; index++) fixture.Controller.UpdateBaseline(Baseline());
        Equal(generation, fixture.Controller.Snapshot.Generation);
    }

    private static void EmptyActionAddsNoAnimation()
    {
        Equal<AnimationIntent?>(null, ActionMapper.TryFrom(null, null));
        Equal<AnimationIntent?>(null, ActionMapper.TryFrom("unknown", "unknown"));
    }

    private static void RendererFaultRetries()
    {
        using var fixture = new Fixture();
        var lastGood = fixture.Renderer.Frames[^1];
        fixture.Renderer.FailNext = true;
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        Equal(lastGood, fixture.Renderer.Frames[^1]);
        fixture.Controller.Tick();
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        Equal("work.write.normal", fixture.Renderer.Frames[^1].PlanId);
    }

    private static void ChatErrorResumesBaseline()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(Request(AnimationIntent.Think, AnimationPriority.Think, "chat-error"));
        fixture.Advance(10);
        fixture.Controller.Complete("chat-error", new AnimationOutcome(IsError: true, Reason: "offline"));
        Equal(("think.normal", AnimationPhaseKind.Exit), fixture.Current);
        fixture.Advance(10);
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void FeedLockIgnoresTouch()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Eat,
            AnimationSource.Feed,
            "feed-lock",
            AnimationPriority.Feed,
            new Dictionary<string, string> { ["item"] = "water" }));
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.TouchHeadReflex,
            AnimationSource.Touch,
            "touch-during-feed",
            AnimationPriority.Touch));
        Equal("feed.drink.normal:water", fixture.Controller.Snapshot.PlanId);
        Equal(0, fixture.Controller.Snapshot.QueuedCount);
    }

    private static void BaselineTransitionQueuesReaction()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        fixture.Controller.Submit(Request(AnimationIntent.Happy, AnimationPriority.Response));
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        Equal(1, fixture.Controller.Snapshot.QueuedCount);
        fixture.Advance(10);
        Equal(("speech.happy", AnimationPhaseKind.Body), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void DisconnectUsesSafeBaseline()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);

        fixture.Controller.UpdateBaseline(new BaselineSnapshot(
            false, true, true, "write", new(true, true, true, true), 1.0));
        Equal(("work.write.normal", AnimationPhaseKind.Exit), fixture.Current);
        fixture.Advance(10);
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);

        fixture.Controller.UpdateBaseline(Baseline() with { IdleHint = "read" });
        Equal(("idle.read.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("idle.read.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void StretchHintFiresOnce()
    {
        using var fixture = new Fixture();
        var stretch = Baseline() with { IdleHint = "stretch" };
        fixture.Controller.UpdateBaseline(stretch);
        Equal("idle.stretch.normal", fixture.Controller.Snapshot.PlanId);
        var generation = fixture.Controller.Snapshot.Generation;
        fixture.Controller.UpdateBaseline(stretch);
        Equal(generation, fixture.Controller.Snapshot.Generation);
    }

    private static void VirtualRandomizedSoak()
    {
        using var fixture = new Fixture();
        var random = new Random(20260712);
        string? chat = null;
        for (var elapsed = 0; elapsed < 30 * 60 * 1000; elapsed += 100)
        {
            if (elapsed % 20_000 == 0)
            {
                var baseline = random.Next(4) switch
                {
                    0 => Baseline(sleeping: true),
                    1 => Baseline(work: true),
                    2 => Baseline() with { IdleHint = "read" },
                    _ => Baseline(),
                };
                fixture.Controller.UpdateBaseline(baseline);
            }
            if (elapsed % 7_300 == 0)
            {
                fixture.Controller.Submit(Request(
                    random.Next(2) == 0 ? AnimationIntent.TouchHeadReflex : AnimationIntent.TouchBodyReflex,
                    AnimationPriority.Touch));
            }
            if (elapsed % 11_100 == 0)
            {
                fixture.Controller.Submit(new AnimationRequest(
                    AnimationIntent.Eat,
                    AnimationSource.Feed,
                    $"soak-feed-{elapsed}",
                    AnimationPriority.Feed,
                    new Dictionary<string, string> { ["item"] = random.Next(2) == 0 ? "curry" : "water" }));
            }
            if (chat is null && elapsed % 17_000 == 0)
            {
                chat = $"soak-chat-{elapsed}";
                fixture.Controller.Submit(Request(AnimationIntent.Think, AnimationPriority.Think, chat));
            }
            else if (chat is not null && elapsed % 17_000 == 8_500)
            {
                fixture.Controller.Complete(chat, new AnimationOutcome(AnimationIntent.Neutral));
                chat = null;
            }
            fixture.Advance(100);
        }
        if (chat is not null) fixture.Controller.Complete(chat, new AnimationOutcome(IsError: true));
        for (var index = 0; index < 100 &&
            (fixture.Controller.Snapshot.QueuedCount > 0 || fixture.Controller.Snapshot.Execution != AnimationExecutionKind.Baseline);
            index++) fixture.Advance(10);
        Equal(AnimationExecutionKind.Baseline, fixture.Controller.Snapshot.Execution!.Value);
        Equal(0, fixture.Controller.Snapshot.QueuedCount);
        if (fixture.Renderer.Frames.Count < 1000) throw new InvalidOperationException("soak 没有推进足够多的合成帧。");
    }

    private static void NonChatThinkIsDropped()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.BridgeResponse,
            "response-think",
            AnimationPriority.Response));
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);
        Equal(false, fixture.Controller.Snapshot.ThinkPending);
    }

    private static void ThinkingReactionDoesNotRestartPending()
    {
        using var fixture = new Fixture();
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            "chat-thinking-reaction",
            AnimationPriority.Think));
        fixture.Advance(10);
        fixture.Controller.Complete("chat-thinking-reaction", new AnimationOutcome(AnimationIntent.Think));
        fixture.Advance(10);
        Equal(("idle.default.normal", AnimationPhaseKind.Body), fixture.Current);
        Equal(false, fixture.Controller.Snapshot.ThinkPending);
    }

    private static void QueuedThinkCompletionRemovesRequest()
    {
        using var fixture = new Fixture();
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        fixture.Controller.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            "queued-chat",
            AnimationPriority.Think));
        Equal(1, fixture.Controller.Snapshot.QueuedCount);
        fixture.Controller.Complete("queued-chat", new AnimationOutcome(AnimationIntent.Happy));
        Equal(false, fixture.Controller.Snapshot.ThinkPending);
        Equal(1, fixture.Controller.Snapshot.QueuedCount);
        fixture.Advance(10);
        Equal(("speech.happy", AnimationPhaseKind.Body), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void RendererFaultFreezesTimeline()
    {
        using var fixture = new Fixture();
        fixture.Renderer.FailuresRemaining = 2;
        fixture.Controller.UpdateBaseline(Baseline(work: true));
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Clock.Advance(100);
        fixture.Controller.Tick();
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Controller.Tick();
        Equal(("work.write.normal", AnimationPhaseKind.Entry), fixture.Current);
        fixture.Advance(10);
        Equal(("work.write.normal", AnimationPhaseKind.Body), fixture.Current);
    }

    private static void TouchCorrelationSurvives()
    {
        using var fixture = new Fixture();
        TouchDetectedEventArgs? committed = null;
        fixture.Controller.TouchDetected += (_, args) => committed = args;
        const string correlation = "touch-correlation";
        fixture.Renderer.RaiseTouchStarted(TouchZone.Head, correlation);
        Equal(correlation, fixture.Controller.Snapshot.CorrelationId);
        fixture.Renderer.RaiseTouchDetected(TouchZone.Head, correlation);
        Equal(correlation, committed?.CorrelationId);
    }

    private static BaselineSnapshot Baseline(bool sleeping = false, bool work = false) =>
        new(true, sleeping, work, "idle", new(false, false, false, false), 0.5);

    private static AnimationRequest Request(
        AnimationIntent intent,
        AnimationPriority priority,
        string? correlation = null) =>
        new(
            intent,
            intent == AnimationIntent.Think ? AnimationSource.Chat : AnimationSource.System,
            correlation ?? Guid.NewGuid().ToString("N"),
            priority);

    private static AnimationPhasePlan Phase(
        AnimationPhaseKind kind,
        bool loop,
        params AnimationLayerPlan[] layers) => new(kind, loop, layers);

    private static AnimationLayerPlan Layer(
        string name,
        int z,
        params (string Path, int Duration)[] frames) =>
        new(name, z, frames.Select(frame => new AnimationFrameSpec(frame.Path, frame.Duration)).ToArray());

    private static void Equal<T>(T expected, T actual)
    {
        if (!EqualityComparer<T>.Default.Equals(expected, actual))
        {
            throw new InvalidOperationException($"expected={expected}, actual={actual}");
        }
    }

    private sealed class Fixture : IDisposable
    {
        private readonly string _temporaryRoot;

        public Fixture()
        {
            _temporaryRoot = CreateAssetRoot();
            Clock = new ManualAnimationClock();
            Renderer = new RecordingRenderer();
            Controller = new AnimationController(BuildManifest(_temporaryRoot), Renderer, Clock, autoStart: false);
        }

        public ManualAnimationClock Clock { get; }
        public RecordingRenderer Renderer { get; }
        public AnimationController Controller { get; }
        public (string, AnimationPhaseKind) Current =>
            (Controller.Snapshot.PlanId!, Controller.Snapshot.Phase!.Value);

        public void Advance(int milliseconds)
        {
            Clock.Advance(milliseconds);
            Controller.Tick();
        }

        public void Dispose()
        {
            Controller.Dispose();
            Directory.Delete(Directory.GetParent(Directory.GetParent(_temporaryRoot)!.FullName)!.FullName, recursive: true);
        }

        private static AnimationManifest BuildManifest(string root)
        {
            var ids = new (string Id, AnimationIntent Intent, bool Baseline, bool Pending, bool Phased)[]
            {
                ("idle.default.normal", AnimationIntent.Idle, true, false, false),
                ("sleep.normal", AnimationIntent.Sleep, true, false, true),
                ("work.write.normal", AnimationIntent.Work, true, false, true),
                ("idle.read.normal", AnimationIntent.Read, true, false, true),
                ("idle.write.normal", AnimationIntent.Write, true, false, true),
                ("idle.gaze.normal", AnimationIntent.Gaze, true, false, true),
                ("idle.stretch.normal", AnimationIntent.Stretch, false, false, false),
                ("think.normal", AnimationIntent.Think, false, true, true),
                ("touch.head.normal", AnimationIntent.TouchHeadReflex, false, false, true),
                ("touch.body.happy", AnimationIntent.TouchBodyReflex, false, false, true),
                ("speech.neutral", AnimationIntent.Neutral, false, false, false),
                ("speech.happy", AnimationIntent.Happy, false, false, false),
                ("speech.alert", AnimationIntent.Alert, false, false, false),
                ("speech.worried", AnimationIntent.Worried, false, false, false),
            };
            var plans = ids.Select(item => new AnimationPlan(
                item.Id,
                item.Intent,
                item.Phased ? OnePhase(item.Id, AnimationPhaseKind.Entry) : null,
                OnePhase(item.Id, AnimationPhaseKind.Body, item.Baseline || item.Pending),
                item.Phased ? OnePhase(item.Id, AnimationPhaseKind.Exit) : null,
                item.Baseline,
                item.Pending)).ToList();
            plans.Add(new AnimationPlan(
                "feed.eat.normal",
                AnimationIntent.Eat,
                null,
                Phase(AnimationPhaseKind.Body, false,
                    Layer("back", 0, ("feed/back", 10)),
                    Layer("front", 2, ("feed/front", 10))),
                null));
            plans.Add(new AnimationPlan(
                "feed.drink.normal",
                AnimationIntent.Eat,
                null,
                Phase(AnimationPhaseKind.Body, false,
                    Layer("back", 0, ("drink/back", 10)),
                    Layer("front", 2, ("drink/front", 10))),
                null));
            return AnimationManifest.Create(root, plans, validate: false);
        }

        private static AnimationPhasePlan OnePhase(string id, AnimationPhaseKind kind, bool loop = false) =>
            Phase(kind, loop, Layer("main", 0, ($"{id}/{kind}", 10)));

        private static string CreateAssetRoot()
        {
            var core = Path.Combine(Path.GetTempPath(), "buddyshell-tests", Guid.NewGuid().ToString("N"), "core");
            var root = Path.Combine(core, "pet", "vup");
            var food = Path.Combine(core, "image", "food");
            Directory.CreateDirectory(root);
            Directory.CreateDirectory(food);
            foreach (var name in new[] { "罗宋汤.png", "番茄意面.png", "奶茶.png", "咖啡饮料.png", "矿泉水.png" })
            {
                File.WriteAllBytes(Path.Combine(food, name), []);
            }
            return root;
        }
    }

    private sealed class RecordingRenderer : Border, IAnimationRenderer
    {
        public UIElement View => this;
        public List<CompositedFrame> Frames { get; } = [];
        public int FailuresRemaining { get; set; }
        public bool FailNext { get => FailuresRemaining > 0; set => FailuresRemaining = value ? 1 : 0; }
        public event EventHandler<TouchDetectedEventArgs>? TouchStarted;
        public event EventHandler<TouchDetectedEventArgs>? TouchDetected;
        public void Render(CompositedFrame frame)
        {
            if (FailuresRemaining > 0)
            {
                FailuresRemaining -= 1;
                throw new InvalidOperationException("synthetic renderer fault");
            }
            Frames.Add(frame);
        }
        public void RaiseTouchStarted(TouchZone zone, string correlation) =>
            TouchStarted?.Invoke(this, new TouchDetectedEventArgs(zone, correlation));
        public void RaiseTouchDetected(TouchZone zone, string correlation) =>
            TouchDetected?.Invoke(this, new TouchDetectedEventArgs(zone, correlation));
        public void SetWarmth(double warmth) { }
        public void Dispose() { }
    }
}
