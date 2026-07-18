using BuddyShell.Bridge;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace BuddyShell;

public static class SettingsStore
{
    private static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static string DataDirectory
    {
        get
        {
            var overridden = Environment.GetEnvironmentVariable("BUDDYSHELL_DATA_DIR");
            return string.IsNullOrWhiteSpace(overridden)
                ? Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "MyBuddy")
                : Path.GetFullPath(overridden);
        }
    }

    public static string SettingsPath => Path.Combine(DataDirectory, "settings.json");

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

    public static bool HasApiKey(ShellSettings settings) =>
        !string.IsNullOrWhiteSpace(settings.ProtectedApiKey);

    public static void SaveApiKey(ShellSettings settings, string apiKey)
    {
        var bytes = Encoding.UTF8.GetBytes(apiKey.Trim());
        settings.ProtectedApiKey = Convert.ToBase64String(Dpapi.Protect(bytes));
        Save(settings);
    }

    public static string ReadApiKey(ShellSettings settings)
    {
        if (string.IsNullOrWhiteSpace(settings.ProtectedApiKey))
            throw new InvalidOperationException("还没有设置 OpenRouter API key。");
        try
        {
            var protectedBytes = Convert.FromBase64String(settings.ProtectedApiKey);
            return Encoding.UTF8.GetString(Dpapi.Unprotect(protectedBytes));
        }
        catch (Exception error) when (error is Win32Exception or FormatException)
        {
            throw new InvalidOperationException("无法读取已保存的 API key，请在设置中重新输入。", error);
        }
    }
}

internal static class Dpapi
{
    private const int UiForbidden = 0x1;

    [StructLayout(LayoutKind.Sequential)]
    private struct DataBlob
    {
        public int Size;
        public IntPtr Data;
    }

    public static byte[] Protect(byte[] value) => Transform(value, protect: true);
    public static byte[] Unprotect(byte[] value) => Transform(value, protect: false);

    private static byte[] Transform(byte[] value, bool protect)
    {
        var input = new DataBlob { Size = value.Length, Data = Marshal.AllocHGlobal(value.Length) };
        var output = new DataBlob();
        try
        {
            Marshal.Copy(value, 0, input.Data, value.Length);
            var succeeded = protect
                ? CryptProtectData(ref input, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, UiForbidden, out output)
                : CryptUnprotectData(ref input, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, UiForbidden, out output);
            if (!succeeded) throw new Win32Exception(Marshal.GetLastWin32Error());
            var result = new byte[output.Size];
            Marshal.Copy(output.Data, result, 0, output.Size);
            return result;
        }
        finally
        {
            Marshal.FreeHGlobal(input.Data);
            if (output.Data != IntPtr.Zero) LocalFree(output.Data);
        }
    }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptProtectData(
        ref DataBlob input, string? description, IntPtr entropy, IntPtr reserved,
        IntPtr prompt, int flags, out DataBlob output);

    [DllImport("crypt32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CryptUnprotectData(
        ref DataBlob input, IntPtr description, IntPtr entropy, IntPtr reserved,
        IntPtr prompt, int flags, out DataBlob output);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr memory);
}
