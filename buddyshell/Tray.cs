namespace BuddyShell;

public sealed class Tray : IDisposable
{
    private readonly System.Windows.Forms.NotifyIcon _icon;
    private readonly System.Windows.Forms.ToolStripMenuItem _work;

    public Tray()
    {
        _work = new System.Windows.Forms.ToolStripMenuItem("陪我干活");
        _work.Click += (_, _) => WorkToggled?.Invoke(this, EventArgs.Empty);
        var settings = new System.Windows.Forms.ToolStripMenuItem("设置");
        settings.Click += (_, _) => SettingsRequested?.Invoke(this, EventArgs.Empty);
        var exit = new System.Windows.Forms.ToolStripMenuItem("退出");
        exit.Click += (_, _) => ExitRequested?.Invoke(this, EventArgs.Empty);
        var menu = new System.Windows.Forms.ContextMenuStrip();
        menu.Items.AddRange([_work, settings, new System.Windows.Forms.ToolStripSeparator(), exit]);
        _icon = new System.Windows.Forms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Information,
            Text = "小布",
            ContextMenuStrip = menu,
            Visible = true,
        };
        _icon.DoubleClick += (_, _) => ShowRequested?.Invoke(this, EventArgs.Empty);
    }

    public event EventHandler? WorkToggled;
    public event EventHandler? SettingsRequested;
    public event EventHandler? ExitRequested;
    public event EventHandler? ShowRequested;

    public void SetWorking(bool active) => _work.Text = active ? "结束陪伴" : "陪我干活";

    public void Dispose()
    {
        _icon.Visible = false;
        _icon.Dispose();
    }
}
