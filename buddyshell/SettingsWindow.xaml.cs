using BuddyShell.Bridge;
using System.Windows;
using System.Windows.Navigation;

namespace BuddyShell;

public partial class SettingsWindow : Window
{
    private readonly ShellSettings _settings;

    public SettingsWindow(ShellSettings settings)
    {
        InitializeComponent();
        _settings = settings;
        IdlePauseMinutes.Text = settings.IdlePauseMinutes.ToString();
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        _settings.IdlePauseMinutes = int.TryParse(IdlePauseMinutes.Text, out var minutes)
            ? Math.Clamp(minutes, 1, 240)
            : 30;
        SettingsStore.Save(_settings);
        DialogResult = true;
    }

    private void ChangeKey_Click(object sender, RoutedEventArgs e)
    {
        var window = new FirstRunWindow(_settings) { Owner = this };
        if (window.ShowDialog() == true)
            MessageBox.Show(this, "新 key 会在重启小布后生效。", "已保存");
    }

    private void AboutLink_RequestNavigate(object sender, RequestNavigateEventArgs e)
    {
        System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(e.Uri.AbsoluteUri)
        {
            UseShellExecute = true,
        });
        e.Handled = true;
    }
}
