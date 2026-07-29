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
        var main = new MainWindow();
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
