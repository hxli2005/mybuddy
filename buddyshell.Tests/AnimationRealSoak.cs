using BuddyShell.Anim;
using System.Diagnostics;
using System.IO;
using System.Text.Json;

namespace BuddyShell.Tests;

internal static class AnimationRealSoak
{
    public static int Run(string petRoot, string outputPath, int durationSeconds)
    {
        var startedAt = DateTimeOffset.Now;
        var stopwatch = Stopwatch.StartNew();
        var errors = new List<string>();
        long transitionCount = 0;
        long lastGeneration = 0;
        var random = new Random(20260712);
        var clock = new SystemAnimationClock();
        var renderer = new FramePlayerHost(petRoot) { Width = 320, Height = 320 };
        var manifest = AnimationManifest.CreateDefault(petRoot);
        using var controller = new AnimationController(manifest, renderer, clock, autoStart: false);
        controller.Faulted += (_, args) =>
        {
            if (!args.Recovered) errors.Add(args.Exception?.GetType().Name ?? "renderer_fault");
        };

        string? pendingChat = null;
        long pendingChatDue = 0;
        long nextAction = 250;
        long nextBaseline = 5_000;
        long nextHeartbeat = 0;
        try
        {
            while (stopwatch.Elapsed.TotalSeconds < durationSeconds)
            {
                controller.Tick();
                var elapsed = stopwatch.ElapsedMilliseconds;
                if (controller.Snapshot.Generation != lastGeneration)
                {
                    lastGeneration = controller.Snapshot.Generation;
                    transitionCount += 1;
                }

                if (pendingChat is not null && elapsed >= pendingChatDue)
                {
                    controller.Complete(
                        pendingChat,
                        new AnimationOutcome(random.Next(3) switch
                        {
                            0 => AnimationIntent.Happy,
                            1 => AnimationIntent.Neutral,
                            _ => null,
                        }));
                    pendingChat = null;
                }

                if (elapsed >= nextBaseline)
                {
                    var baseline = random.Next(5);
                    controller.UpdateBaseline(new BaselineSnapshot(
                        true,
                        baseline == 0,
                        baseline == 1,
                        baseline switch { 2 => "read", 3 => "write", 4 => "gaze", _ => "idle" },
                        new(false, false, false, false),
                        random.NextDouble()));
                    nextBaseline = elapsed + random.Next(18_000, 35_001);
                }

                if (elapsed >= nextAction)
                {
                    var id = $"soak-{elapsed}";
                    switch (random.Next(6))
                    {
                        case 0:
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.TouchHeadReflex,
                                AnimationSource.Touch,
                                id,
                                AnimationPriority.Touch));
                            break;
                        case 1:
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.TouchBodyReflex,
                                AnimationSource.Touch,
                                id,
                                AnimationPriority.Touch));
                            break;
                        case 2:
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.Eat,
                                AnimationSource.Feed,
                                id,
                                AnimationPriority.Feed,
                                new Dictionary<string, string>
                                {
                                    ["item"] = new[] { "congee", "curry", "milk_tea", "coffee", "water" }[random.Next(5)],
                                }));
                            break;
                        case 3 when pendingChat is null:
                            pendingChat = id;
                            pendingChatDue = elapsed + random.Next(900, 3_001);
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.Think,
                                AnimationSource.Chat,
                                id,
                                AnimationPriority.Think));
                            break;
                        case 4:
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.Happy,
                                AnimationSource.BridgeResponse,
                                id,
                                AnimationPriority.Response));
                            break;
                        default:
                            controller.Submit(new AnimationRequest(
                                AnimationIntent.Stretch,
                                AnimationSource.System,
                                id,
                                AnimationPriority.IdleFlourish));
                            break;
                    }
                    nextAction = elapsed + random.Next(650, 2_501);
                }

                if (elapsed >= nextHeartbeat)
                {
                    WriteEvidence(outputPath, startedAt, stopwatch, durationSeconds, controller, renderer,
                        transitionCount, errors, complete: false);
                    nextHeartbeat = elapsed + 10_000;
                }
                Thread.Sleep(16);
            }
        }
        catch (Exception exception)
        {
            errors.Add(exception.ToString());
        }

        WriteEvidence(outputPath, startedAt, stopwatch, durationSeconds, controller, renderer,
            transitionCount, errors, complete: true);
        return errors.Count == 0 && stopwatch.Elapsed.TotalSeconds >= durationSeconds ? 0 : 1;
    }

    private static void WriteEvidence(
        string outputPath,
        DateTimeOffset startedAt,
        Stopwatch stopwatch,
        int durationSeconds,
        AnimationController controller,
        FramePlayerHost renderer,
        long transitionCount,
        IReadOnlyList<string> errors,
        bool complete)
    {
        var path = Path.GetFullPath(outputPath);
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var process = Process.GetCurrentProcess();
        var data = new
        {
            mode = "real_time_random_actions",
            renderer = "FramePlayerHost",
            asset_root = controller.AssetRoot,
            started_at = startedAt,
            checked_at = DateTimeOffset.Now,
            running_seconds = (int)stopwatch.Elapsed.TotalSeconds,
            target_seconds = durationSeconds,
            responding = true,
            working_set_bytes = process.WorkingSet64,
            transition_count = transitionCount,
            current = controller.Snapshot,
            composited_layers = renderer.LastFrame?.Layers.Count ?? 0,
            cached_frame_count = renderer.CachedFrameCount,
            cached_frame_bytes = renderer.CachedFrameBytes,
            errors,
            stable = complete && stopwatch.Elapsed.TotalSeconds >= durationSeconds && errors.Count == 0,
        };
        var temporary = path + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true }));
        File.Move(temporary, path, overwrite: true);
    }
}
