using System.IO;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using VPet_Simulator.Core;

namespace BuddyShell.Anim;

public sealed class VPetCoreHost : UserControl, IAnimationHost, IAnimationHostDiagnostics
{
    private readonly Border _surface;
    private readonly GraphCore _graphCore;
    private readonly string _petRoot;
    private PNGAnimation? _current;
    private AnimationIntent _baseline = AnimationIntent.Idle;
    private bool _disposed;
    private int _currentFrameCount;
    private DateTimeOffset _pressStarted;
    private Point _pressPoint;
    private TouchZone _pressZone;
    private bool _pointerMoved;

    public VPetCoreHost(string petRoot)
    {
        _petRoot = petRoot;
        _surface = new Border
        {
            Background = System.Windows.Media.Brushes.Transparent,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch,
        };
        Content = _surface;
        _graphCore = new GraphCore(125, Dispatcher);
        MouseLeftButtonDown += OnMouseLeftButtonDown;
        MouseMove += OnMouseMove;
        MouseLeftButtonUp += OnMouseLeftButtonUp;
    }

    public UIElement View => this;
    public string HostName => nameof(VPetCoreHost);
    public string AssetRoot => _petRoot;
    public int CurrentFrameCount => _currentFrameCount;
    public bool IsPlaying => _current is not null && !_disposed;

    public event EventHandler<TouchDetectedEventArgs>? TouchDetected;

    public void Play(AnimationIntent intent, bool loop = false)
    {
        if (_disposed) return;
        Dispatcher.Invoke(() => PlayCore(intent, loop));
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

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _current?.Dispose();
        _graphCore.Dispose();
    }

    private void PlayCore(AnimationIntent intent, bool loop)
    {
        var (folder, graphType, animatType) = Resolve(intent);
        var files = Directory.EnumerateFiles(folder, "*.png", SearchOption.TopDirectoryOnly)
            .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
            .Select(path => new FileInfo(path))
            .ToArray();
        _currentFrameCount = files.Length;
        if (files.Length == 0)
        {
            throw new InvalidDataException($"动画目录没有 PNG:{folder}");
        }

        _current?.Dispose();
        var info = new GraphInfo(intent.ToString(), graphType, animatType, IGameSave.ModeType.Nomal);
        _current = new PNGAnimation(_graphCore, folder, files, info, loop);
        _current.Run(_surface, () =>
        {
            if (!loop && !_disposed)
            {
                Play(_baseline, loop: true);
            }
        });
    }

    private (string Folder, GraphInfo.GraphType Type, GraphInfo.AnimatType Animat) Resolve(
        AnimationIntent intent)
    {
        return intent switch
        {
            AnimationIntent.TouchHeadReflex => (
                AssetLocator.FindAnimationFolder(_petRoot, "Touch_Head"),
                GraphInfo.GraphType.Touch_Head,
                GraphInfo.AnimatType.Single),
            AnimationIntent.TouchBodyReflex => (
                AssetLocator.FindAnimationFolder(_petRoot, "Touch_Body"),
                GraphInfo.GraphType.Touch_Body,
                GraphInfo.AnimatType.Single),
            AnimationIntent.Sleep or AnimationIntent.Nap => (
                AssetLocator.FindAnimationFolder(_petRoot, "Sleep"),
                GraphInfo.GraphType.Sleep,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Think => (
                AssetLocator.FindAnimationFolder(_petRoot, "Think"),
                GraphInfo.GraphType.StateONE,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Eat => (
                AssetLocator.FindAnimationFolder(_petRoot, "Eat"),
                GraphInfo.GraphType.StateONE,
                GraphInfo.AnimatType.Single),
            AnimationIntent.Read => (
                AssetLocator.FindAnimationFolder(_petRoot, @"WORK\Study", "WORK"),
                GraphInfo.GraphType.StateONE,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Write => (
                AssetLocator.FindAnimationFolder(_petRoot, @"WORK\Calligraphy", "WORK"),
                GraphInfo.GraphType.StateONE,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Work => (
                AssetLocator.FindAnimationFolder(_petRoot, @"WORK\WorkONE", "WORK"),
                GraphInfo.GraphType.Idel,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Gaze => (
                AssetLocator.FindAnimationFolder(_petRoot, @"IDEL\aside", "IDEL"),
                GraphInfo.GraphType.Idel,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Stretch => (
                AssetLocator.FindAnimationFolder(_petRoot, @"IDEL\yawning", "IDEL"),
                GraphInfo.GraphType.Idel,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Happy => (
                AssetLocator.FindAnimationFolder(_petRoot, @"IDEL\Meow\Happy", "IDEL"),
                GraphInfo.GraphType.Idel,
                GraphInfo.AnimatType.B_Loop),
            AnimationIntent.Sad or AnimationIntent.Worried => (
                AssetLocator.FindAnimationFolder(_petRoot, @"IDEL\aside\PoorCondition", "IDEL"),
                GraphInfo.GraphType.Idel,
                GraphInfo.AnimatType.B_Loop),
            _ => (
                AssetLocator.FindAnimationFolder(_petRoot, "Default"),
                GraphInfo.GraphType.Default,
                GraphInfo.AnimatType.B_Loop),
        };
    }

    private void OnMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        var point = e.GetPosition(this);
        _pressStarted = DateTimeOffset.UtcNow;
        _pressPoint = point;
        _pressZone = ClassifyTouch(point.Y, ActualHeight);
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
            $"latency_ms={stopwatch.Elapsed.TotalMilliseconds:F2} host=VPetCoreHost");
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
}
