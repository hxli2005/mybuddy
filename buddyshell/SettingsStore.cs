using BuddyShell.Bridge;
using System.IO;
using System.Text.Json;

namespace BuddyShell;

public static class SettingsStore
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static string SettingsPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "BuddyShell",
        "settings.json");

    public static ShellSettings Load()
    {
        try
        {
            return File.Exists(SettingsPath)
                ? JsonSerializer.Deserialize<ShellSettings>(File.ReadAllText(SettingsPath), Json) ?? new()
                : new();
        }
        catch (Exception exception)
        {
            App.LogException(exception);
            return new();
        }
    }

    public static void Save(ShellSettings settings)
    {
        var directory = Path.GetDirectoryName(SettingsPath)!;
        Directory.CreateDirectory(directory);
        var temporary = SettingsPath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(settings, Json));
        File.Move(temporary, SettingsPath, overwrite: true);
    }
}
