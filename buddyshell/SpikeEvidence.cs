using BuddyShell.Anim;
using System.IO;
using System.Text.Json;
using System.Windows.Threading;

namespace BuddyShell;

public sealed class SpikeEvidence : IDisposable
{
    private readonly string? _path = Environment.GetEnvironmentVariable("BUDDYSHELL_SPIKE_EVIDENCE");
    private readonly IAnimationDiagnostics _host;
    private readonly DateTimeOffset _startedAt = DateTimeOffset.Now;
    private readonly int _unhandledAtStart = App.UnhandledExceptionCount;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(10) };

    public SpikeEvidence(IAnimationController host)
    {
        _host = host as IAnimationDiagnostics
            ?? throw new InvalidOperationException("动画宿主未提供 spike 诊断接口。");
        _timer.Tick += (_, _) => Write();
        _timer.Start();
        Write();
    }

    public void Dispose()
    {
        _timer.Stop();
        Write();
    }

    private void Write()
    {
        if (string.IsNullOrWhiteSpace(_path)) return;
        try
        {
            var elapsed = DateTimeOffset.Now - _startedAt;
            var head = _host.ClassifyTouch(10, 100);
            var body = _host.ClassifyTouch(90, 100);
            var unhandled = App.UnhandledExceptionCount - _unhandledAtStart;
            var data = new
            {
                host = _host.HostName,
                asset_root = _host.AssetRoot,
                asset_visible = Directory.Exists(_host.AssetRoot) && _host.CurrentFrameCount > 0,
                current_frame_count = _host.CurrentFrameCount,
                idle_playing = _host.IsPlaying,
                touch_head_zone = head.ToString(),
                touch_body_zone = body.ToString(),
                touch_zones_distinct = head != body,
                started_at = _startedAt,
                checked_at = DateTimeOffset.Now,
                running_seconds = (int)elapsed.TotalSeconds,
                unhandled_exceptions = unhandled,
                stable_30_minutes = elapsed >= TimeSpan.FromMinutes(30) && unhandled == 0,
            };
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(_path))!);
            var temporary = _path + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temporary, _path, overwrite: true);
        }
        catch (Exception exception)
        {
            App.LogException(exception);
        }
    }
}
