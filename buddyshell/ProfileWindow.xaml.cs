using BuddyShell.Bridge;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Threading;

namespace BuddyShell;

public partial class ProfileWindow : Window
{
    private readonly TimeSpan? _autoCloseAfter;

    public ProfileWindow(IReadOnlyList<UserProfileItem> items, TimeSpan? autoCloseAfter = null)
    {
        InitializeComponent();
        _autoCloseAfter = autoCloseAfter;
        ProfileItems.ItemsSource = items;
        CountText.Text = items.Count == 0 ? "0 条长期事实" : $"{items.Count} 条长期事实";
        EmptyState.Visibility = items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        ProfileScroll.Visibility = items.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
        if (_autoCloseAfter is not null) Loaded += RunAutoDemoAsync;
    }

    private async void RunAutoDemoAsync(object sender, RoutedEventArgs e)
    {
        Loaded -= RunAutoDemoAsync;
        await Task.Delay(1800);
        await Dispatcher.InvokeAsync(() =>
        {
            var firstSource = FindVisualChild<Expander>(ProfileItems);
            if (firstSource is not null) firstSource.IsExpanded = true;
        }, DispatcherPriority.Loaded);
        await Task.Delay(_autoCloseAfter!.Value);
        if (IsVisible) Close();
    }

    private static T? FindVisualChild<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(parent); index++)
        {
            var child = VisualTreeHelper.GetChild(parent, index);
            if (child is T found) return found;
            var nested = FindVisualChild<T>(child);
            if (nested is not null) return nested;
        }
        return null;
    }
}
