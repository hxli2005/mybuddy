using Microsoft.Win32;
using System.IO;

namespace BuddyShell.Anim;

public static class AssetLocator
{
    public static string FindPetRoot()
    {
        var explicitRoot = Environment.GetEnvironmentVariable("BUDDYSHELL_PET_ROOT");
        var candidates = new List<string?>
        {
            explicitRoot,
            Path.Combine(AppContext.BaseDirectory, "assets", "pet"),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "assets", "pet")),
            FindSteamPetRoot(),
            @"D:\steam\steamapps\common\VPet\mod\0000_core\pet\vup",
        };
        var found = candidates.FirstOrDefault(path =>
            !string.IsNullOrWhiteSpace(path) && Directory.Exists(path));
        if (found is null)
        {
            throw new DirectoryNotFoundException(
                "找不到 VPet 素材。请设置 BUDDYSHELL_PET_ROOT 指向 0000_core/pet/vup。"
            );
        }
        return Path.GetFullPath(found);
    }

    private static string? FindSteamPetRoot()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            var steamPath = key?.GetValue("SteamPath")?.ToString();
            if (string.IsNullOrWhiteSpace(steamPath)) return null;
            return Path.Combine(
                steamPath,
                "steamapps",
                "common",
                "VPet",
                "mod",
                "0000_core",
                "pet",
                "vup");
        }
        catch
        {
            return null;
        }
    }
}
