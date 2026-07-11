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

    public static string FindAnimationFolder(string root, params string[] categories)
    {
        foreach (var category in categories)
        {
            var categoryRoot = Path.Combine(root, category);
            if (!Directory.Exists(categoryRoot))
            {
                continue;
            }
            var folders = Directory.EnumerateDirectories(categoryRoot, "*", SearchOption.AllDirectories)
                .Prepend(categoryRoot)
                .Where(path => Directory.EnumerateFiles(path, "*.png", SearchOption.TopDirectoryOnly).Any())
                .OrderByDescending(ScoreFolder)
                .ThenBy(path => path.Length)
                .ToList();
            if (folders.Count > 0)
            {
                return folders[0];
            }
        }
        throw new DirectoryNotFoundException($"素材中缺少动画目录:{string.Join('/', categories)}");
    }

    private static int ScoreFolder(string path)
    {
        var score = 0;
        if (path.Contains("Nomal", StringComparison.OrdinalIgnoreCase)) score += 20;
        if (path.Contains("Happy", StringComparison.OrdinalIgnoreCase)) score += 10;
        if (path.Contains("Ill", StringComparison.OrdinalIgnoreCase)) score -= 20;
        if (path.Contains("Poor", StringComparison.OrdinalIgnoreCase)) score -= 10;
        return score;
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
