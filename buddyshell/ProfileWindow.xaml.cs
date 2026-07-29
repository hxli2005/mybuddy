using BuddyShell.Bridge;
using System.Windows;

namespace BuddyShell;

public partial class ProfileWindow : Window
{
    public ProfileWindow(IReadOnlyList<UserProfileItem> items)
    {
        InitializeComponent();
        ProfileItems.ItemsSource = items;
        CountText.Text = items.Count == 0 ? "0 条长期事实" : $"{items.Count} 条长期事实";
        EmptyState.Visibility = items.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        ProfileScroll.Visibility = items.Count == 0 ? Visibility.Collapsed : Visibility.Visible;
    }
}
