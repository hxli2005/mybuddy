using BuddyShell.Bridge;
using System.Windows;

namespace BuddyShell;

public partial class FirstRunWindow : Window
{
    private readonly ShellSettings _settings;

    public FirstRunWindow(ShellSettings? settings = null)
    {
        InitializeComponent();
        _settings = settings ?? SettingsStore.Load();
        Loaded += (_, _) => ApiKey.Focus();
    }

    private void Start_Click(object sender, RoutedEventArgs e)
    {
        var key = ApiKey.Password.Trim();
        if (key.Length < 10)
        {
            MessageBox.Show(this, "请粘贴完整的 OpenRouter API key。", "还差一步");
            return;
        }
        try
        {
            SettingsStore.SaveApiKey(_settings, key);
            DialogResult = true;
        }
        catch (Exception error)
        {
            App.LogException(error);
            MessageBox.Show(this, $"保存 key 失败：{error.Message}", "无法保存");
        }
    }
}
