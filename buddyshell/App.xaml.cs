using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Threading;

namespace BuddyShell;

public partial class App : Application
{
    private Mutex? _singleInstance;
    internal static int UnhandledExceptionCount { get; private set; }

    protected override void OnStartup(StartupEventArgs e)
    {
        _singleInstance = new Mutex(true, "BuddyShell.MyBuddy.Singleton", out var created);
        if (!created)
        {
            Shutdown();
            return;
        }

        DispatcherUnhandledException += OnDispatcherUnhandledException;
        AppDomain.CurrentDomain.UnhandledException += (_, args) =>
        {
            UnhandledExceptionCount += 1;
            LogException(args.ExceptionObject as Exception ?? new Exception("Unknown fatal error"));
        };
        base.OnStartup(e);

        var mentorDemo = e.Args.Any(
            value => string.Equals(value, "--mentor-demo", StringComparison.OrdinalIgnoreCase));
        if (mentorDemo)
        {
            var dataDirectoryIndex = Array.FindIndex(
                e.Args,
                value => string.Equals(
                    value,
                    "--shell-data-dir",
                    StringComparison.OrdinalIgnoreCase));
            if (dataDirectoryIndex >= 0 && dataDirectoryIndex + 1 < e.Args.Length)
            {
                var dataDirectory = Path.GetFullPath(e.Args[dataDirectoryIndex + 1]);
                Environment.SetEnvironmentVariable("BUDDYSHELL_DATA_DIR", dataDirectory);
            }
        }
        var settings = SettingsStore.Load();
        if (!SettingsStore.HasApiKey(settings))
        {
            var firstRun = new FirstRunWindow();
            if (firstRun.ShowDialog() != true)
            {
                Shutdown();
                return;
            }
        }
        var main = new MainWindow(mentorDemo);
        MainWindow = main;
        ShutdownMode = ShutdownMode.OnMainWindowClose;
        main.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _singleInstance?.Dispose();
        base.OnExit(e);
    }

    private void OnDispatcherUnhandledException(
        object sender,
        DispatcherUnhandledExceptionEventArgs e)
    {
        LogException(e.Exception);
        UnhandledExceptionCount += 1;
        e.Handled = true;
        if (MainWindow is MainWindow window)
        {
            window.SetConnectionState("程序出了点小问题,已记录", ConnectionState.Error);
        }
    }

    internal static void LogException(Exception exception)
        => LogMessage(exception.ToString());

    internal static void LogMessage(string message)
    {
        try
        {
            var directory = Path.Combine(SettingsStore.DataDirectory, "logs");
            Directory.CreateDirectory(directory);
            File.AppendAllText(
                Path.Combine(directory, $"{DateTime.Now:yyyy-MM-dd}.log"),
                $"[{DateTimeOffset.Now:o}] {message}\n");
        }
        catch
        {
            Debug.WriteLine(message);
        }
    }
}
