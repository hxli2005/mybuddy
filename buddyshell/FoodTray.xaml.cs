using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace BuddyShell;

public sealed class FoodSelectedEventArgs(string itemId) : EventArgs
{
    public string ItemId { get; } = itemId;
}

public partial class FoodTray : UserControl
{
    private Point _dragStart;
    private DateTimeOffset _lastDragCompleted;

    public FoodTray()
    {
        InitializeComponent();
        PreviewMouseLeftButtonDown += (_, args) => _dragStart = args.GetPosition(this);
        PreviewMouseMove += OnMouseMove;
    }

    public event EventHandler<FoodSelectedEventArgs>? FoodSelected;

    private void Food_Click(object sender, RoutedEventArgs e)
    {
        if (DateTimeOffset.UtcNow - _lastDragCompleted < TimeSpan.FromMilliseconds(500)) return;
        if (sender is Button { Tag: string itemId })
        {
            FoodSelected?.Invoke(this, new FoodSelectedEventArgs(itemId));
        }
    }

    private void OnMouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || e.OriginalSource is not FrameworkElement element) return;
        var point = e.GetPosition(this);
        if (Math.Abs(point.X - _dragStart.X) < SystemParameters.MinimumHorizontalDragDistance &&
            Math.Abs(point.Y - _dragStart.Y) < SystemParameters.MinimumVerticalDragDistance) return;
        if (element.DataContext is string item)
        {
            System.Windows.DragDrop.DoDragDrop(this, item, DragDropEffects.Copy);
            _lastDragCompleted = DateTimeOffset.UtcNow;
        }
        else if (FindButton(element) is { Tag: string tag })
        {
            System.Windows.DragDrop.DoDragDrop(this, tag, DragDropEffects.Copy);
            _lastDragCompleted = DateTimeOffset.UtcNow;
        }
    }

    private static Button? FindButton(DependencyObject? value)
    {
        while (value is not null)
        {
            if (value is Button button) return button;
            value = System.Windows.Media.VisualTreeHelper.GetParent(value);
        }
        return null;
    }
}
