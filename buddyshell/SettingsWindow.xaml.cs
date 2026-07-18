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
        BridgeUrl.Text = settings.BridgeUrl;
        PetAssetRoot.Text = settings.PetAssetRoot ?? "";
        IdlePauseMinutes.Text = settings.IdlePauseMinutes.ToString();
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        _settings.BridgeUrl = BridgeUrl.Text.Trim();
        _settings.PetAssetRoot = string.IsNullOrWhiteSpace(PetAssetRoot.Text)
            ? null
            : PetAssetRoot.Text.Trim();
        _settings.IdlePauseMinutes = int.TryParse(IdlePauseMinutes.Text, out var minutes)
            ? Math.Clamp(minutes, 1, 240)
            : 30;
        SettingsStore.Save(_settings);
        DialogResult = true;
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
