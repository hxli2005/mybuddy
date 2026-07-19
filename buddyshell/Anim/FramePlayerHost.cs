using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace BuddyShell.Anim;

public sealed class FramePlayerHost : UserControl, IAnimationRenderer, IAnimationDiagnostics
{
    private readonly Grid _surface = new();
    private readonly FrameCache _cache = new();
    private readonly Dictionary<string, Image> _images = new(StringComparer.Ordinal);
    private readonly string _petRoot;
    private DateTimeOffset _pressStarted;
    private Point _pressPoint;
    private TouchZone _pressZone;
    private string _pressCorrelationId = "";
    private bool _pointerMoved;
    private int _currentFrameCount;

    public FramePlayerHost(string petRoot)
    {
        _petRoot = petRoot;
        Content = _surface;
        MouseLeftButtonDown += OnMouseLeftButtonDown;
        MouseMove += OnMouseMove;
        MouseLeftButtonUp += OnMouseLeftButtonUp;
    }

    public UIElement View => this;
    public string HostName => nameof(FramePlayerHost);
    public string AssetRoot => _petRoot;
    public int CurrentFrameCount => _currentFrameCount;
    public bool IsPlaying => _currentFrameCount > 0;
    public CompositedFrame? LastFrame { get; private set; }
    public int CachedFrameCount => _cache.Count;
    public long CachedFrameBytes => _cache.EstimatedBytes;

    public event EventHandler<TouchDetectedEventArgs>? TouchStarted;
    public event EventHandler<TouchDetectedEventArgs>? TouchDetected;
    public event EventHandler? DragStarted;

    public void Render(CompositedFrame frame)
    {
        Dispatcher.VerifyAccess();
        // Decode every layer first. A failed layer therefore leaves the previous
        // complete composited frame visible instead of producing a half-updated pet.
        var decoded = frame.Layers.ToDictionary(
            layer => layer.Name,
            layer => _cache.Get(layer.SourcePath),
            StringComparer.Ordinal);
        var activeNames = frame.Layers.Select(layer => layer.Name).ToHashSet(StringComparer.Ordinal);
        foreach (var stale in _images.Keys.Where(name => !activeNames.Contains(name)).ToArray())
        {
            _surface.Children.Remove(_images[stale]);
            _images.Remove(stale);
        }

        foreach (var layer in frame.Layers.OrderBy(item => item.ZIndex))
        {
            if (!_images.TryGetValue(layer.Name, out var image))
            {
                image = new Image { Stretch = Stretch.Uniform };
                _images[layer.Name] = image;
                _surface.Children.Add(image);
            }
            Panel.SetZIndex(image, layer.ZIndex);
            ApplyPlacement(image, layer);
            image.Source = decoded[layer.Name];
        }
        _currentFrameCount = frame.Layers.Count;
        LastFrame = frame;
    }

    public void Dispose()
    {
        ReleaseMouseCapture();
        _surface.Children.Clear();
        _images.Clear();
        _cache.Clear();
        _currentFrameCount = 0;
        LastFrame = null;
    }

    public TouchZone ClassifyTouch(double y, double height) =>
        y <= height * 0.45 ? TouchZone.Head : TouchZone.Body;

    private void ApplyPlacement(Image image, RenderLayer layer)
    {
        var placement = layer.Placement;
        image.Visibility = layer.Visible ? Visibility.Visible : Visibility.Collapsed;
        image.Opacity = Math.Clamp(layer.Opacity, 0, 1);
        image.RenderTransformOrigin = new Point(0.5, 0.5);
        image.LayoutTransform = layer.Rotation == 0
            ? Transform.Identity
            : new RotateTransform(layer.Rotation);
        image.HorizontalAlignment = HorizontalAlignment.Center;
        image.VerticalAlignment = VerticalAlignment.Center;
        if (placement is null)
        {
            image.Width = double.NaN;
            image.Height = double.NaN;
            image.Margin = new Thickness(0);
            image.RenderTransform = Transform.Identity;
            return;
        }

        if (placement.CoordinateSpace == LayerCoordinateSpace.LogicalCanvas)
        {
            var surfaceWidth = PositiveSize(_surface.ActualWidth, ActualWidth, Width);
            var surfaceHeight = PositiveSize(_surface.ActualHeight, ActualHeight, Height);
            var displaySize = Math.Min(surfaceWidth, surfaceHeight);
            var scale = displaySize / placement.CanvasSize;
            var originX = (surfaceWidth - displaySize) / 2;
            var originY = (surfaceHeight - displaySize) / 2;
            image.HorizontalAlignment = HorizontalAlignment.Left;
            image.VerticalAlignment = VerticalAlignment.Top;
            image.Width = placement.Width * scale;
            image.Height = placement.Height * scale;
            image.Margin = new Thickness(
                originX + placement.OffsetX * scale,
                originY + placement.OffsetY * scale,
                0,
                0);
            image.RenderTransform = Transform.Identity;
            return;
        }
        image.Width = placement.Width;
        image.Height = placement.Height;
        image.Margin = new Thickness(0);
        image.RenderTransform = new TranslateTransform(placement.OffsetX, placement.OffsetY);
    }

    private static double PositiveSize(params double[] candidates) =>
        candidates.FirstOrDefault(value => double.IsFinite(value) && value > 0, 500);

    private void OnMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _pressStarted = DateTimeOffset.UtcNow;
        _pressPoint = e.GetPosition(this);
        _pressZone = ClassifyTouch(_pressPoint.Y, ActualHeight);
        _pressCorrelationId = Guid.NewGuid().ToString("N");
        _pointerMoved = false;
        CaptureMouse();
        var stopwatch = Stopwatch.StartNew();
        TouchStarted?.Invoke(this, new TouchDetectedEventArgs(_pressZone, _pressCorrelationId));
        stopwatch.Stop();
        App.LogMessage(
            $"event=touch_reflex zone={_pressZone.ToString().ToLowerInvariant()} " +
            $"latency_ms={stopwatch.Elapsed.TotalMilliseconds:F2} host={HostName}");
        e.Handled = true;
    }

    private void OnMouseMove(object sender, MouseEventArgs e)
    {
        if (!IsMouseCaptured || e.LeftButton != MouseButtonState.Pressed) return;
        var point = e.GetPosition(this);
        if (!_pointerMoved &&
            (Math.Abs(point.X - _pressPoint.X) >= SystemParameters.MinimumHorizontalDragDistance ||
             Math.Abs(point.Y - _pressPoint.Y) >= SystemParameters.MinimumVerticalDragDistance))
        {
            _pointerMoved = true;
            ReleaseMouseCapture();
            DragStarted?.Invoke(this, EventArgs.Empty);
            e.Handled = true;
        }
    }

    private void OnMouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        if (!IsMouseCaptured) return;
        ReleaseMouseCapture();
        var duration = DateTimeOffset.UtcNow - _pressStarted;
        if (!_pointerMoved && duration <= TimeSpan.FromMilliseconds(1200))
        {
            TouchDetected?.Invoke(this, new TouchDetectedEventArgs(_pressZone, _pressCorrelationId));
        }
        e.Handled = true;
    }
}
