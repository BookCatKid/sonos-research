using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security;
using System.Text;
using System.Windows.Threading;
using System.Xml;
using Sonos.Controller.Desktop.Logging;
using Sonos.Controller.Desktop.Utilities;

namespace Sonos.Controller.Desktop.SCLib;

public static class FileSystem
{
	private const string jffsDirectoryName = "jffs";

	private const string localSettingsFileName = "localsettings.txt";

	private const string uiDataFileName = "uidata.xml";

	private const string runtimeDirectoryName = "runtime";

	private const string managedSharesFileName = "managedshares.xml";

	private const string crashReportsFileName = "crashreports.txt";

	private const string sonosLaunchUtilityFileName = "SonosLaunchUtility.exe";

	private const string installErrorFileName = "InstallError.txt";

	private const string installSuccessFileName = "InstallSuccess.txt";

	private const string installLogFileName = "Installer.log";

	private const string languageFileName = "Language.txt";

	private const string anacapaDirectoryName = "anacapa";

	private const string anacapaConfigFileName = "anacapa.conf";

	private const string anacapaMimeTypesFileName = "mime.types";

	private const string resourceFileDirectoryName = "resources";

	private const string flutterDataDirectoryName = "flutter_data";

	private const string downloadResourceFileDirectoryName = "cache";

	private const string anacapaConfigFileDirectoryName = "conf";

	private const string sonosRootDirectoryName = "SonosV2,_Inc";

	private const string resourceNameNamespace = "Sonos.Controller.Desktop.SCLib.Resources.";

	private const string rcbDirectoryName = "sys\\run\\rcb";

	private static string allUsersApplicationDataDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "SonosV2,_Inc");

	private static string jffsDirectory = Path.Combine(allUsersApplicationDataDirectory, "jffs");

	private static string localSettingsFile = Path.Combine(JffsDirectory, "localsettings.txt");

	private static string uiDataFile = Path.Combine(JffsDirectory, "uidata.xml");

	private static string runtimeDirectory = Path.Combine(allUsersApplicationDataDirectory, "runtime");

	private static string permanentDataFile = Path.Combine(RuntimeDirectory, "uidata.xml");

	private static string managedSharesFile = Path.Combine(RuntimeDirectory, "managedshares.xml");

	private static string crashReportsFile = Path.Combine(RuntimeDirectory, "crashreports.txt");

	private static string sonosLaunchUtilityFile = Path.Combine(RuntimeDirectory, "SonosLaunchUtility.exe");

	private static string installErrorFile = Path.Combine(RuntimeDirectory, "InstallError.txt");

	private static string installSuccessFile = Path.Combine(RuntimeDirectory, "InstallSuccess.txt");

	private static string installLogFile = Path.Combine(RuntimeDirectory, "Installer.log");

	private static string languageFile = Path.Combine(RuntimeDirectory, "Language.txt");

	private static string resourcesDirectory = Path.Combine(allUsersApplicationDataDirectory, "resources");

	private static string downloadResourcesDirectory = Path.Combine(allUsersApplicationDataDirectory, "cache");

	private static string anacapaDirectory = Path.Combine(allUsersApplicationDataDirectory, "anacapa");

	private static string anacapaConfigFile = Path.Combine(AnacapaDirectory, Path.Combine("conf", "anacapa.conf"));

	private static bool directoriesInitialized = false;

	private static bool filesPopulated = false;

	private static bool needToCreateResources = false;

	private static bool needToCreateAnacapa = false;

	private static bool? didThisRunStartAfterInstallSuccess;

	private static DispatcherTimer ensureCacheTimer = new DispatcherTimer
	{
		Interval = TimeSpan.FromSeconds(1.0)
	};

	public static string AllUsersApplicationDataDirectory => allUsersApplicationDataDirectory;

	public static string JffsDirectory => jffsDirectory;

	public static string LocalSettingsFile => localSettingsFile;

	public static string UIDataFile => uiDataFile;

	public static string RuntimeDirectory => runtimeDirectory;

	public static string PermanentDataFile => permanentDataFile;

	public static string DefaultLogDirectory => RuntimeDirectory;

	public static string ManagedSharesFile => managedSharesFile;

	public static string CrashReportsFile => crashReportsFile;

	public static string SonosLaunchUtilityFile => sonosLaunchUtilityFile;

	public static string InstallErrorFile => installErrorFile;

	public static string InstallSuccessFile => installSuccessFile;

	public static string InstallLogFile => installLogFile;

	public static string LanguageFile => languageFile;

	public static string ResourcesDirectory => resourcesDirectory;

	public static string FlutterDataDirectoryName => "flutter_data";

	public static string DownloadResourcesDirectory => downloadResourcesDirectory;

	public static string AnacapaDirectory => anacapaDirectory;

	public static string AnacapaConfigFile => anacapaConfigFile;

	private static DispatcherTimer EnsureCacheTimer => ensureCacheTimer;

	public static void BeginningOfTime()
	{
		directoriesInitialized = false;
		filesPopulated = false;
		needToCreateResources = false;
		needToCreateAnacapa = false;
		didThisRunStartAfterInstallSuccess = null;
		FileUtility.RemoveDirectory(ResourcesDirectory);
		FileUtility.RemoveDirectory(AnacapaDirectory);
		FileUtility.EnsureDirectory(AllUsersApplicationDataDirectory, setPermissionsOnExisting: true);
		FileUtility.RemoveFile(SonosLaunchUtilityFile);
	}

	public static void InitializeDirectories()
	{
		if (!directoriesInitialized)
		{
			needToCreateResources = !Directory.Exists(ResourcesDirectory);
			needToCreateAnacapa = !Directory.Exists(AnacapaDirectory);
			FileUtility.EnsureDirectory(JffsDirectory, setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(Path.Combine(JffsDirectory, "sys\\run\\rcb"), setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(ResourcesDirectory, setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(DownloadResourcesDirectory, setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(AnacapaDirectory, setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(Path.Combine(AnacapaDirectory, "conf"), setPermissionsOnExisting: false);
			FileUtility.EnsureDirectory(RuntimeDirectory, setPermissionsOnExisting: false);
			directoriesInitialized = true;
		}
	}

	public static void PopulateFiles()
	{
		if (filesPopulated)
		{
			return;
		}
		InitializeDirectories();
		if (needToCreateResources)
		{
			try
			{
				CopyManifestToFile("Sonos.Controller.Desktop.SCLib.Resources.libutils.so", Path.Combine(resourcesDirectory, "libutils.so"));
			}
			catch
			{
			}
			try
			{
				CopyManifestToFile("Sonos.Controller.Desktop.SCLib.Resources.ctrlMetricsConfig.xml", Path.Combine(resourcesDirectory, "reportmgr", "ctrlMetricsConfig.xml"));
			}
			catch
			{
			}
			try
			{
				CopyManifestToFile("Sonos.Controller.Desktop.SCLib.Resources.voice.localeSupport.alexa.utterances.json", Path.Combine(resourcesDirectory, "voice\\localeSupport\\alexa", "utterances.json"));
			}
			catch
			{
			}
		}
		string text = RootCertBundleFilepath();
		if (!File.Exists(text))
		{
			try
			{
				CopyManifestToFile("Sonos.Controller.Desktop.SCLib.Resources.cert_bundle.rcb", text);
			}
			catch
			{
			}
		}
		if (needToCreateAnacapa)
		{
			string path = Path.Combine(AnacapaDirectory, "conf");
			string text2 = typeof(FileSystem).Assembly.GetManifestResourceNames().FirstOrDefault((string n) => n == "Sonos.Controller.Desktop.SCLib.Resources.anacapa.conf");
			if (string.IsNullOrEmpty(text2))
			{
				LogManager.Logger.LogError("Anacapa config file was not found in the manifest.");
			}
			else
			{
				FormatManifestToFile(text2, AnacapaConfigFile, AnacapaDirectory);
			}
			string fileName = Path.Combine(path, "mime.types");
			string text3 = typeof(FileSystem).Assembly.GetManifestResourceNames().FirstOrDefault((string n) => n == "Sonos.Controller.Desktop.SCLib.Resources.mime.types");
			if (string.IsNullOrEmpty(text3))
			{
				LogManager.Logger.LogError("Anacapa mime types file was not found in the manifest.");
			}
			else
			{
				CopyManifestToFile(text3, fileName);
			}
		}
		if (!File.Exists(ManagedSharesFile))
		{
			using (FileUtility.EnsureFile(ManagedSharesFile, FileMode.CreateNew, setPermissionsOnExisting: true))
			{
			}
		}
		filesPopulated = true;
	}

	public static void EnsureCache()
	{
		EnsureCacheInTheFuture();
	}

	private static void EnsureCacheInTheFuture()
	{
		if (EnsureCacheTimer.IsEnabled)
		{
			EnsureCacheTimer.Stop();
		}
		else
		{
			EnsureCacheTimer.Tick += EnsureCacheTimer_Tick;
		}
		EnsureCacheTimer.Start();
	}

	private static void EnsureCacheTimer_Tick(object sender, EventArgs e)
	{
		EnsureCacheTimer.Tick -= EnsureCacheTimer_Tick;
		EnsureCacheTimer.Stop();
		try
		{
			IEnumerable<FileInfo> enumerable = new DirectoryInfo(DownloadResourcesDirectory).EnumerateFiles();
			if (enumerable == null)
			{
				return;
			}
			foreach (FileInfo item in enumerable)
			{
				if (item != null && !string.IsNullOrEmpty(item.FullName))
				{
					using (FileUtility.EnsureFile(item.FullName, FileMode.Open, setPermissionsOnExisting: true))
					{
					}
				}
			}
		}
		catch (SecurityException ex)
		{
			LogManager.Logger.LogWarning(ex, "Could not ensure cache.");
		}
		catch (NotSupportedException ex2)
		{
			LogManager.Logger.LogWarning(ex2, "Could not ensure cache.");
		}
	}

	public static bool BackupFiles()
	{
		return FileUtility.MoveDirectory(AllUsersApplicationDataDirectory, AllUsersApplicationDataDirectory + ".bak");
	}

	public static bool RestoreFiles()
	{
		return FileUtility.MoveDirectory(AllUsersApplicationDataDirectory + ".bak", AllUsersApplicationDataDirectory);
	}

	public static bool RemoveDirectories()
	{
		return RemoveDirectories(removeAllDirectories: false);
	}

	public static bool RemoveDirectories(bool removeAllDirectories)
	{
		bool flag = true;
		if (removeAllDirectories)
		{
			return FileUtility.RemoveDirectory(AllUsersApplicationDataDirectory);
		}
		flag = FileUtility.RemoveDirectory(JffsDirectory);
		flag = FileUtility.RemoveDirectory(ResourcesDirectory) && flag;
		flag = FileUtility.RemoveDirectory(DownloadResourcesDirectory) && flag;
		return FileUtility.RemoveDirectory(AnacapaDirectory) && flag;
	}

	public static bool UninstallScrubFiles()
	{
		return RemoveDirectories(removeAllDirectories: true);
	}

	public static Dictionary<string, string> LoadUIData()
	{
		//IL_0098: Expected O, but got Unknown
		//IL_000b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0010: Unknown result type (might be due to invalid IL or missing references)
		//IL_001c: Expected O, but got Unknown
		Dictionary<string, string> dictionary = new Dictionary<string, string>();
		try
		{
			XmlReader val = XmlReader.Create(UIDataFile, new XmlReaderSettings
			{
				IgnoreWhitespace = true
			});
			try
			{
				while (val.Read())
				{
					if (!val.IsStartElement("Item"))
					{
						continue;
					}
					val.ReadStartElement("Item");
					string text = val.ReadElementString("Name");
					if (text != null)
					{
						text = text.Trim();
						string text2 = val.ReadElementString("Value");
						if (text2 != null)
						{
							text2 = text2.Trim();
						}
						dictionary.Add(text, text2);
					}
				}
			}
			finally
			{
				((IDisposable)val)?.Dispose();
			}
		}
		catch (IOException ex)
		{
			LogManager.Logger.LogWarning(ex, "Could not load UI Data, such as the machine unique identifier.");
		}
		catch (XmlException ex2)
		{
			XmlException ex3 = ex2;
			LogManager.Logger.LogWarning((Exception)(object)ex3, "Could not load UI Data, such as the machine unique identifier.");
		}
		return dictionary;
	}

	public static void SaveUIData(Dictionary<string, string> data)
	{
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(UIDataFile, FileMode.Create, setPermissionsOnExisting: false);
			if (fileStream == null)
			{
				return;
			}
			XmlWriter val = XmlWriter.Create((Stream)fileStream);
			try
			{
				fileStream = null;
				val.WriteStartDocument();
				val.WriteStartElement("Items");
				foreach (KeyValuePair<string, string> datum in data)
				{
					val.WriteStartElement("Item");
					val.WriteElementString("Name", datum.Key);
					val.WriteElementString("Value", datum.Value);
					val.WriteEndElement();
				}
				val.WriteEndElement();
				val.WriteEndDocument();
			}
			finally
			{
				((IDisposable)val)?.Dispose();
			}
		}
		catch (IOException ex)
		{
			LogManager.Logger.LogError(ex);
		}
		finally
		{
			fileStream?.Dispose();
		}
	}

	public static Dictionary<string, string> LoadPermanentData()
	{
		//IL_0098: Expected O, but got Unknown
		//IL_000b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0010: Unknown result type (might be due to invalid IL or missing references)
		//IL_001c: Expected O, but got Unknown
		Dictionary<string, string> dictionary = new Dictionary<string, string>();
		try
		{
			XmlReader val = XmlReader.Create(PermanentDataFile, new XmlReaderSettings
			{
				IgnoreWhitespace = true
			});
			try
			{
				while (val.Read())
				{
					if (!val.IsStartElement("Item"))
					{
						continue;
					}
					val.ReadStartElement("Item");
					string text = val.ReadElementString("Name");
					if (text != null)
					{
						text = text.Trim();
						string text2 = val.ReadElementString("Value");
						if (text2 != null)
						{
							text2 = text2.Trim();
						}
						dictionary.Add(text, text2);
					}
				}
			}
			finally
			{
				((IDisposable)val)?.Dispose();
			}
		}
		catch (IOException ex)
		{
			LogManager.Logger.LogWarning(ex, "Could not load Permanent Data, such as the machine unique identifier.");
		}
		catch (XmlException ex2)
		{
			XmlException ex3 = ex2;
			LogManager.Logger.LogWarning((Exception)(object)ex3, "Could not load Permanent Data, such as the machine unique identifier.");
		}
		return dictionary;
	}

	public static void SavePermanentData(Dictionary<string, string> data)
	{
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(PermanentDataFile, FileMode.Create, setPermissionsOnExisting: false);
			if (fileStream == null)
			{
				return;
			}
			XmlWriter val = XmlWriter.Create((Stream)fileStream);
			try
			{
				fileStream = null;
				val.WriteStartDocument();
				val.WriteStartElement("Items");
				foreach (KeyValuePair<string, string> datum in data)
				{
					val.WriteStartElement("Item");
					val.WriteElementString("Name", datum.Key);
					val.WriteElementString("Value", datum.Value);
					val.WriteEndElement();
				}
				val.WriteEndElement();
				val.WriteEndDocument();
			}
			finally
			{
				((IDisposable)val)?.Dispose();
			}
		}
		catch (IOException ex)
		{
			LogManager.Logger.LogError(ex);
		}
		finally
		{
			fileStream?.Dispose();
		}
	}

	public static void LoadManagedShares()
	{
		//IL_0173: Expected O, but got Unknown
		//IL_003b: Unknown result type (might be due to invalid IL or missing references)
		//IL_0040: Unknown result type (might be due to invalid IL or missing references)
		//IL_004c: Expected O, but got Unknown
		ShareTracker.Singleton.Shares.Clear();
		try
		{
			if (!File.Exists(ManagedSharesFile) || new FileInfo(ManagedSharesFile).Length <= 0)
			{
				return;
			}
			XmlReader val = XmlReader.Create(ManagedSharesFile, new XmlReaderSettings
			{
				IgnoreWhitespace = true
			});
			try
			{
				while (val.Read())
				{
					if (val.IsStartElement("WdcrShouldFixSharePermissions"))
					{
						bool shouldFixSharePermissions = val.ReadElementContentAsBoolean();
						ShareTracker.Singleton.ShouldFixSharePermissions = shouldFixSharePermissions;
					}
					if (val.IsStartElement("WdcrManagedShare"))
					{
						val.ReadStartElement("WdcrManagedShare");
						string text = val.ReadElementString("NetName");
						if (text != null)
						{
							text = text.Trim();
						}
						string text2 = val.ReadElementString("LocalPath");
						if (text2 != null)
						{
							text2 = text2.Trim();
						}
						string text3 = val.ReadElementString("UncPath");
						if (text3 != null)
						{
							text3 = text3.Trim();
						}
						string text4 = val.ReadElementString("UserName");
						if (text4 != null)
						{
							text4 = text4.Trim();
						}
						bool result = false;
						string text5 = null;
						try
						{
							text5 = val.ReadElementString("IsHttpShare");
						}
						catch (XmlException)
						{
						}
						if (text5 == null)
						{
							text5 = false.ToString();
						}
						bool.TryParse(text5, out result);
						if (text != null && text3 != null && text2 != null && text4 != null)
						{
							ShareRecord share = new ShareRecord(text2, text, text3, text4, result);
							ShareTracker.Singleton.AddShare(share);
						}
					}
				}
			}
			finally
			{
				((IDisposable)val)?.Dispose();
			}
		}
		catch (IOException ex2)
		{
			LogManager.Logger.LogWarning(ex2, "Could not load managed share information.");
		}
		catch (XmlException ex3)
		{
			XmlException ex4 = ex3;
			LogManager.Logger.LogWarning((Exception)(object)ex4, "Could not load managed share information.");
		}
	}

	public static void SaveManagedShares()
	{
		FileStream fileStream = null;
		XmlWriter val = null;
		try
		{
			fileStream = FileUtility.EnsureFile(ManagedSharesFile, FileMode.Create, setPermissionsOnExisting: false);
			if (fileStream == null)
			{
				return;
			}
			XmlWriter val2 = (val = XmlWriter.Create((Stream)fileStream));
			try
			{
				fileStream = null;
				val.WriteStartDocument();
				val.WriteStartElement("WdcrManagedShares");
				val.WriteStartElement("WdcrShouldFixSharePermissions");
				val.WriteValue(ShareTracker.Singleton.ShouldFixSharePermissions);
				val.WriteEndElement();
				foreach (ShareRecord share in ShareTracker.Singleton.Shares)
				{
					if (share != null)
					{
						val.WriteStartElement("WdcrManagedShare");
						val.WriteElementString("NetName", share.NetName);
						val.WriteElementString("LocalPath", share.LocalPath);
						val.WriteElementString("UncPath", share.UncPath);
						val.WriteElementString("UserName", share.UserName);
						val.WriteElementString("IsHttpShare", share.IsLocalHttpShare.ToString());
						val.WriteEndElement();
					}
				}
				val.WriteEndElement();
				val.WriteEndDocument();
			}
			finally
			{
				((IDisposable)val2)?.Dispose();
			}
		}
		catch (IOException ex)
		{
			LogManager.Logger.LogError(ex);
		}
		finally
		{
			fileStream?.Dispose();
		}
	}

	public static string EmitLaunchUtility()
	{
		FileUtility.RemoveFile(SonosLaunchUtilityFile);
		string text = typeof(FileSystem).Assembly.GetManifestResourceNames().FirstOrDefault((string n) => n.Contains("SonosLaunchUtility.exe"));
		if (string.IsNullOrEmpty(text))
		{
			LogManager.Logger.LogError("SonosLaunchUtility.exe was not found in the manifest.");
			return null;
		}
		if (CopyManifestToFile(text, SonosLaunchUtilityFile))
		{
			return SonosLaunchUtilityFile;
		}
		return null;
	}

	public static void WriteCrashReport(string crashReport)
	{
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(CrashReportsFile, FileMode.Append, setPermissionsOnExisting: false);
			if (fileStream != null)
			{
				using (StreamWriter streamWriter = new StreamWriter(fileStream))
				{
					fileStream = null;
					streamWriter.Write(crashReport);
					return;
				}
			}
		}
		finally
		{
			fileStream?.Close();
		}
	}

	public static string ReadCrashReports()
	{
		if (File.Exists(CrashReportsFile))
		{
			string result = null;
			FileStream fileStream = null;
			try
			{
				fileStream = FileUtility.EnsureFile(CrashReportsFile, FileMode.Open, setPermissionsOnExisting: false);
				if (fileStream != null)
				{
					using StreamReader streamReader = new StreamReader(fileStream);
					fileStream = null;
					result = streamReader.ReadToEnd();
				}
				FileUtility.RemoveFile(CrashReportsFile);
				return result;
			}
			finally
			{
				fileStream?.Close();
			}
		}
		return null;
	}

	private static bool DeleteInstallerFromUpgradeIfPresent()
	{
		bool result = false;
		if (Directory.Exists(RuntimeDirectory))
		{
			string[] files = Directory.GetFiles(RuntimeDirectory, "Sonos_Controller_*.exe");
			if (files != null)
			{
				string[] array = files;
				foreach (string text in array)
				{
					if (!string.IsNullOrEmpty(text))
					{
						result = true;
						FileUtility.RemoveFile(Path.GetFullPath(text));
					}
				}
			}
		}
		return result;
	}

	public static bool InstallErrorIsPresent()
	{
		return File.Exists(InstallErrorFile);
	}

	public static string ReadAndDeleteInstallError()
	{
		string result = null;
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(InstallErrorFile, FileMode.Open, setPermissionsOnExisting: true);
			if (fileStream != null)
			{
				using StreamReader streamReader = new StreamReader(fileStream);
				fileStream = null;
				result = streamReader.ReadToEnd();
			}
		}
		finally
		{
			fileStream?.Dispose();
		}
		FileUtility.RemoveFile(InstallErrorFile);
		return result;
	}

	public static bool DidThisRunStartAfterInstallSuccess()
	{
		if (!didThisRunStartAfterInstallSuccess.HasValue)
		{
			didThisRunStartAfterInstallSuccess = DeleteInstallSuccessIfPresent() || DeleteInstallerFromUpgradeIfPresent();
		}
		return didThisRunStartAfterInstallSuccess.Value;
	}

	private static bool DeleteInstallSuccessIfPresent()
	{
		bool result = File.Exists(InstallSuccessFile);
		FileUtility.RemoveFile(InstallSuccessFile);
		return result;
	}

	public static string ReadInstallLog()
	{
		string result = null;
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(InstallLogFile, FileMode.Open, setPermissionsOnExisting: true);
			if (fileStream != null)
			{
				using StreamReader streamReader = new StreamReader(fileStream);
				fileStream = null;
				result = streamReader.ReadToEnd();
			}
		}
		finally
		{
			fileStream?.Dispose();
		}
		return result;
	}

	public static CultureInfo LoadLanguage()
	{
		if (!File.Exists(LanguageFile))
		{
			return null;
		}
		string cultureName = null;
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(LanguageFile, FileMode.Open, setPermissionsOnExisting: false);
			if (fileStream == null)
			{
				return null;
			}
			using StreamReader streamReader = new StreamReader(fileStream);
			fileStream = null;
			cultureName = streamReader.ReadLine();
		}
		finally
		{
			fileStream?.Close();
		}
		return SafeGetCultureInfo(cultureName);
	}

	public static CultureInfo SafeGetCultureInfo(string cultureName)
	{
		if (string.IsNullOrEmpty(cultureName))
		{
			return null;
		}
		CultureInfo cultureInfo = null;
		try
		{
			return CultureInfo.GetCultureInfo(cultureName);
		}
		catch (ArgumentException)
		{
			return null;
		}
	}

	public static bool SaveLanguage(int cultureIdentifier)
	{
		CultureInfo cultureInfo = null;
		try
		{
			if (cultureIdentifier >= 0)
			{
				cultureInfo = CultureInfo.GetCultureInfo(cultureIdentifier);
			}
		}
		catch (ArgumentException)
		{
		}
		if (cultureInfo == null)
		{
			return false;
		}
		return SaveLanguage(cultureInfo.Name);
	}

	public static bool SaveLanguage(string cultureName)
	{
		if (cultureName == null)
		{
			return false;
		}
		if (cultureName.Length > 5)
		{
			cultureName = cultureName.Substring(0, 5);
		}
		FileStream fileStream = null;
		try
		{
			fileStream = FileUtility.EnsureFile(LanguageFile, FileMode.Create, setPermissionsOnExisting: false);
			if (fileStream == null)
			{
				return false;
			}
			using StreamWriter streamWriter = new StreamWriter(fileStream);
			fileStream = null;
			streamWriter.WriteLine(cultureName);
			return true;
		}
		finally
		{
			fileStream?.Close();
		}
	}

	private static bool CopyManifestToFile(string resourceName, string fileName)
	{
		if (!File.Exists(fileName))
		{
			using Stream stream = typeof(FileSystem).Assembly.GetManifestResourceStream(resourceName);
			if (stream == null)
			{
				throw new InvalidOperationException("Resource stream is null.");
			}
			using FileStream fileStream = FileUtility.EnsureFile(fileName, FileMode.Create, setPermissionsOnExisting: false);
			if (fileStream != null)
			{
				stream.CopyTo(fileStream);
				return true;
			}
			LogManager.Logger.LogError(delegate(FormatMessageHandler m)
			{
				m("Unable to create the file: {0}", fileName);
			});
		}
		return false;
	}

	private static bool FormatManifestToFile(string resourceName, string fileName, params object[] args)
	{
		if (!File.Exists(fileName))
		{
			Stream stream = null;
			try
			{
				stream = typeof(FileSystem).Assembly.GetManifestResourceStream(resourceName);
				using StreamReader streamReader = new StreamReader(stream);
				stream = null;
				string format = streamReader.ReadToEnd();
				string s = string.Format(CultureInfo.InvariantCulture, format, args);
				byte[] bytes = Encoding.ASCII.GetBytes(s);
				using FileStream fileStream = FileUtility.EnsureFile(fileName, FileMode.Create, setPermissionsOnExisting: false);
				if (fileStream != null)
				{
					fileStream.Write(bytes, 0, bytes.Length);
					return true;
				}
				LogManager.Logger.LogError(delegate(FormatMessageHandler m)
				{
					m("Unable to create the file: {0}", fileName);
				});
			}
			finally
			{
				stream?.Dispose();
			}
		}
		return false;
	}

	private static string RootCertBundleFilepath()
	{
		string path = Path.Combine(JffsDirectory, "sys\\run\\rcb");
		string text = null;
		string name = "Sonos.Controller.Desktop.SCLib.Resources.cert_bundle_version.txt";
		using (StreamReader streamReader = new StreamReader(typeof(FileSystem).Assembly.GetManifestResourceStream(name)))
		{
			if (streamReader == null)
			{
				return Path.Combine(path, "trusted_roots.rcb");
			}
			text = streamReader.ReadToEnd();
		}
		if (string.IsNullOrEmpty(text))
		{
			return Path.Combine(path, "trusted_roots.rcb");
		}
		string text2 = null;
		string text3 = null;
		string[] array = text.Split(new char[1] { '\n' });
		for (int i = 0; i < array.Length; i++)
		{
			string[] array2 = array[i].Split(new char[1] { ':' });
			if (array2.Length >= 2)
			{
				string text4 = array2[0].Trim();
				string text5 = array2[1].Trim();
				if (text4.Equals("version"))
				{
					text2 = text5;
				}
				else if (text4.Equals("id"))
				{
					text3 = text5;
				}
			}
		}
		if (string.IsNullOrEmpty(text2) || string.IsNullOrEmpty(text3))
		{
			return Path.Combine(path, "trusted_roots.rcb");
		}
		return Path.Combine(path, text2 + "-" + text3 + ".rcb");
	}
}
