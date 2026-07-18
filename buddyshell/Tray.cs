namespace BuddyShell;

public sealed class Tray : IDisposable
{
    private readonly System.Windows.Forms.NotifyIcon _icon;

    public Tray()
    {
        var settings = new System.Windows.Forms.ToolStripMenuItem("设置");
        settings.Click += (_, _) => SettingsRequested?.Invoke(this, EventArgs.Empty);
        var exit = new System.Windows.Forms.ToolStripMenuItem("退出");
        exit.Click += (_, _) => ExitRequested?.Invoke(this, EventArgs.Empty);
        var menu = new System.Windows.Forms.ContextMenuStrip();
        menu.Items.AddRange([settings, new System.Windows.Forms.ToolStripSeparator(), exit]);
        _icon = new System.Windows.Forms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Information,
            Text = "小布",
            ContextMenuStrip = menu,
            Visible = true,
        };
        _icon.DoubleClick += (_, _) => ShowRequested?.Invoke(this, EventArgs.Empty);
    }

    public event EventHandler? SettingsRequested;
    public event EventHandler? ExitRequested;
    public event EventHandler? ShowRequested;

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}
