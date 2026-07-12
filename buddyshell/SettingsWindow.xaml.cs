using BuddyShell.Bridge;
using System.Windows;
using System.Windows.Navigation;

namespace BuddyShell;

public partial class SettingsWindow : Window
{
    private readonly ShellSettings _settings;

    public SettingsWindow(ShellSettings settings, VPetStateResponse? state)
    {
        InitializeComponent();
        _settings = settings;
        BridgeUrl.Text = settings.BridgeUrl;
        BridgeToken.Password = settings.BridgeToken;
        PhysioInjection.IsChecked = settings.PhysioInjection;
        TouchEscalation.IsChecked = settings.TouchEscalation;
        PhysicalProactive.IsChecked = settings.PhysicalProactive;
        if (state is not null)
        {
            ServerClock.Text = $"服务端时间：{state.ServerTime}";
            Offset.Text = $"验收偏移：{state.TimeOffsetMinutes} 分钟";
        }
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        _settings.BridgeUrl = BridgeUrl.Text.Trim();
        _settings.BridgeToken = BridgeToken.Password.Trim();
        _settings.PhysioInjection = PhysioInjection.IsChecked == true;
        _settings.TouchEscalation = TouchEscalation.IsChecked == true;
        _settings.PhysicalProactive = PhysicalProactive.IsChecked == true;
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
