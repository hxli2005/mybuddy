using System.IO;
using System.Diagnostics;
using System.Text.RegularExpressions;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace BuddyShell.Anim;

public sealed partial class FramePlayerHost : UserControl, IAnimationHost, IAnimationHostDiagnostics
{
    private readonly Image _image = new() { Stretch = System.Windows.Media.Stretch.Uniform };
    private readonly DispatcherTimer _timer = new();
    private readonly string _petRoot;
    private List<string> _frames = [];
    private int _index;
    private bool _loop;
    private AnimationIntent _baseline = AnimationIntent.Idle;
    private DateTimeOffset _pressStarted;
    private Point _pressPoint;
    private TouchZone _pressZone;
    private bool _pointerMoved;

    public FramePlayerHost(string petRoot)
    {
        _petRoot = petRoot;
        Content = _image;
        _timer.Tick += OnTick;
        MouseLeftButtonDown += OnMouseLeftButtonDown;
        MouseMove += OnMouseMove;
        MouseLeftButtonUp += OnMouseLeftButtonUp;
    }

    public UIElement View => this;
    public string HostName => nameof(FramePlayerHost);
    public string AssetRoot => _petRoot;
    public int CurrentFrameCount => _frames.Count;
    public bool IsPlaying => _timer.IsEnabled && _frames.Count > 0;

    public event EventHandler<TouchDetectedEventArgs>? TouchDetected;

    public void Play(AnimationIntent intent, bool loop = false)
    {
        Dispatcher.Invoke(() =>
        {
            var category = intent switch
            {
                AnimationIntent.TouchHeadReflex => new[] { "Touch_Head" },
                AnimationIntent.TouchBodyReflex => new[] { "Touch_Body" },
                AnimationIntent.Sleep or AnimationIntent.Nap => new[] { "Sleep" },
                AnimationIntent.Think => new[] { "Think" },
                AnimationIntent.Eat => new[] { "Eat" },
                AnimationIntent.Read => new[] { @"WORK\Study", "WORK" },
                AnimationIntent.Write => new[] { @"WORK\Calligraphy", "WORK" },
                AnimationIntent.Work => new[] { @"WORK\WorkONE", "WORK" },
                AnimationIntent.Gaze => new[] { @"IDEL\aside", "IDEL" },
                AnimationIntent.Stretch => new[] { @"IDEL\yawning", "IDEL" },
                AnimationIntent.Happy => new[] { @"IDEL\Meow\Happy", "IDEL" },
                AnimationIntent.Sad or AnimationIntent.Worried =>
                    new[] { @"IDEL\aside\PoorCondition", "IDEL" },
                _ => new[] { "Default" },
            };
            var folder = AssetLocator.FindAnimationFolder(_petRoot, category);
            _frames = Directory.EnumerateFiles(folder, "*.png", SearchOption.TopDirectoryOnly)
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                .ToList();
            if (_frames.Count == 0) throw new InvalidDataException($"动画目录没有 PNG:{folder}");
            _index = 0;
            _loop = loop;
            ShowCurrentFrame();
            _timer.Start();
        });
    }

    public void SetBaseline(PhysioLevels levels, double warmth)
    {
        _baseline = levels.Tired ? AnimationIntent.Nap
            : levels.Hungry ? AnimationIntent.Gaze
            : levels.Low ? AnimationIntent.Sad
            : levels.Bright ? AnimationIntent.Happy
            : AnimationIntent.Idle;
        var scale = 0.92 + 0.08 * Math.Clamp(warmth, 0, 1);
        RenderTransformOrigin = new Point(0.5, 1);
        RenderTransform = new System.Windows.Media.ScaleTransform(scale, scale);
    }

    public void Dispose() => _timer.Stop();

    private void OnTick(object? sender, EventArgs e)
    {
        _index += 1;
        if (_index >= _frames.Count)
        {
            if (_loop) _index = 0;
            else
            {
                Play(_baseline, loop: true);
                return;
            }
        }
        ShowCurrentFrame();
    }

    private void ShowCurrentFrame()
    {
        var path = _frames[_index];
        var bitmap = new BitmapImage();
        bitmap.BeginInit();
        bitmap.CacheOption = BitmapCacheOption.OnLoad;
        bitmap.UriSource = new Uri(path, UriKind.Absolute);
        bitmap.EndInit();
        bitmap.Freeze();
        _image.Source = bitmap;
        var match = DurationPattern().Match(Path.GetFileNameWithoutExtension(path));
        _timer.Interval = TimeSpan.FromMilliseconds(
            match.Success && int.TryParse(match.Groups[1].Value, out var ms) ? ms : 125);
    }

    private void OnMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _pressStarted = DateTimeOffset.UtcNow;
        _pressPoint = e.GetPosition(this);
        _pressZone = ClassifyTouch(_pressPoint.Y, ActualHeight);
        _pointerMoved = false;
        CaptureMouse();
        var stopwatch = Stopwatch.StartNew();
        Play(
            _pressZone == TouchZone.Head
                ? AnimationIntent.TouchHeadReflex
                : AnimationIntent.TouchBodyReflex);
        stopwatch.Stop();
        App.LogMessage(
            $"event=touch_reflex zone={_pressZone.ToString().ToLowerInvariant()} " +
            $"latency_ms={stopwatch.Elapsed.TotalMilliseconds:F2} host=FramePlayerHost");
        e.Handled = true;
    }

    private void OnMouseMove(object sender, MouseEventArgs e)
    {
        if (!IsMouseCaptured || e.LeftButton != MouseButtonState.Pressed) return;
        var point = e.GetPosition(this);
        if (Math.Abs(point.X - _pressPoint.X) >= SystemParameters.MinimumHorizontalDragDistance ||
            Math.Abs(point.Y - _pressPoint.Y) >= SystemParameters.MinimumVerticalDragDistance)
        {
            _pointerMoved = true;
        }
    }

    private void OnMouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (!IsMouseCaptured) return;
        ReleaseMouseCapture();
        var duration = DateTimeOffset.UtcNow - _pressStarted;
        if (!_pointerMoved && duration <= TimeSpan.FromMilliseconds(1200))
        {
            TouchDetected?.Invoke(this, new TouchDetectedEventArgs(_pressZone));
        }
        e.Handled = true;
    }

    public TouchZone ClassifyTouch(double y, double height) =>
        y <= height * 0.45 ? TouchZone.Head : TouchZone.Body;

    [GeneratedRegex(@"_(\d+)$")]
    private static partial Regex DurationPattern();
}
