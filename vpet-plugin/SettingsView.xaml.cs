using System;
using System.Windows;

namespace MyBuddy.VPetPlugin;

public partial class SettingsView : Window
{
    private BridgeSettings _current = new();

    public SettingsView()
    {
        InitializeComponent();
    }

    public event EventHandler<SettingsSavedEventArgs>? SaveRequested;

    public void LoadFromSettings(BridgeSettings settings)
    {
        _current = settings;
        BridgeUrlBox.Text = settings.BridgeUrl;
        TokenBox.Password = settings.BridgeToken;
        BodyStateInjectionBox.IsChecked = settings.BodyStateInjection;
        TouchEscalationBox.IsChecked = settings.TouchEscalation;
        PhysicalProactiveBox.IsChecked = settings.PhysicalProactive;
        TodayQuietBox.IsChecked = settings.TodayQuiet;
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        var updated = new BridgeSettings
        {
            BridgeUrl = string.IsNullOrWhiteSpace(BridgeUrlBox.Text)
                ? "http://127.0.0.1:8000"
                : BridgeUrlBox.Text.Trim(),
            BridgeToken = TokenBox.Password.Trim(),
            BodyStateInjection = BodyStateInjectionBox.IsChecked == true,
            TouchEscalation = TouchEscalationBox.IsChecked == true,
            PhysicalProactive = PhysicalProactiveBox.IsChecked == true,
            TodayQuiet = TodayQuietBox.IsChecked == true,
            IdlePauseMinutes = _current.IdlePauseMinutes,
            DrainPollSeconds = _current.DrainPollSeconds,
            PresencePollSeconds = _current.PresencePollSeconds,
            PhysicalCooldownMinutes = _current.PhysicalCooldownMinutes,
            PhysicalDailyLimit = _current.PhysicalDailyLimit,
        };
        SaveRequested?.Invoke(this, new SettingsSavedEventArgs(updated));
        DialogResult = true;
        Close();
    }

    private void Cancel_Click(object sender, RoutedEventArgs e)
    {
        DialogResult = false;
        Close();
    }
}

public sealed class SettingsSavedEventArgs : EventArgs
{
    public SettingsSavedEventArgs(BridgeSettings settings)
    {
        Settings = settings;
    }

    public BridgeSettings Settings { get; }
}
