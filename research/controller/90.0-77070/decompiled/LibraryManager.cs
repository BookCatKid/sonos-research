using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Windows.Forms;
using System.Windows.Interop;
using System.Windows.Threading;
using System.Xml.Serialization;
using Sonos.Controller.Desktop.Actions;
using Sonos.Controller.Desktop.Debug;
using Sonos.Controller.Desktop.Logging;
using Sonos.Controller.Desktop.Utilities;
using Sonos.SCLib.Interop;
using Sonos.SCLib.Interop.Utils;
using SonosAdmWrapper;

namespace Sonos.Controller.Desktop.SCLib;

public class LibraryManager
{
	private struct UniqueIdentifiers
	{
		public string MachineIdentifier { get; set; }

		public string MacAddress { get; set; }
	}

	private SCLibParameters parameters;

	private LogCallback logCallback;

	private AssertionFailureCallback assertionFailureCallback;

	private UIThreadCallback uiThreadCallback;

	private DiagnosticExtraInfoCallback diagnosticExtraInfoCallback;

	private DiagnosticConsoleLogCallback diagnosticConsoleLogCallback;

	private TruncatedStringsCallback truncatedStringsCallback;

	private AutomationData automationData = new AutomationData();

	private PlatformDateTimeProvider platformDateTimeProvider;

	private NetworkManagementCallback networkManagementCallback;

	private CustomSubWizardCallback customSubWizardCallback;

	private SavedDataProvider savedDataProvider;

	private string hostModel;

	private bool recycling;

	public SCILibrary Library { get; private set; }

	public Dispatcher UIThreadDispatcher { get; set; }

	public ActionFactory ActionFactory { get; private set; }

	public DelegateFactory delegateFactory { get; private set; }

	private string HostModel
	{
		get
		{
			if (hostModel == null)
			{
				StringBuilder stringBuilder = new StringBuilder();
				stringBuilder.Append("WDCR");
				if (OSVersionHelper.MajorVersion < 6 || (OSVersionHelper.MajorVersion == 6 && OSVersionHelper.MinorVersion <= 1))
				{
					stringBuilder.Append("_6_1");
				}
				if (!string.IsNullOrEmpty(OSVersionHelper.VersionString))
				{
					stringBuilder.Append(":");
					stringBuilder.Append(OSVersionHelper.VersionString);
				}
				hostModel = stringBuilder.ToString();
			}
			return hostModel;
		}
	}

	public string LanguageID { get; private set; }

	private SCINetworkManagement NetworkManagement
	{
		get
		{
			if (Library == null)
			{
				LogManager.Logger.LogError("The Library is not present.");
				return null;
			}
			SCINetworkManagement sCINetworkManagement = Library.QueryInterface<SCINetworkManagement>();
			if (sCINetworkManagement == null)
			{
				LogManager.Logger.LogError("Could not get the SCINetworkManagement interface. Communication will be problematic.");
			}
			return sCINetworkManagement;
		}
	}

	private void ContractInvariants()
	{
	}

	public void Initialize()
	{
		try
		{
			FileSystem.InitializeDirectories();
			FileSystem.PopulateFiles();
			logCallback = new LogCallback
			{
				Handler = Log
			};
			assertionFailureCallback = new AssertionFailureCallback
			{
				Handler = AssertionFailure
			};
			uiThreadCallback = new UIThreadCallback
			{
				Handler = CallUIThread
			};
			diagnosticExtraInfoCallback = new DiagnosticExtraInfoCallback
			{
				Handler = GetExtraInfoDiagnostics
			};
			diagnosticConsoleLogCallback = new DiagnosticConsoleLogCallback
			{
				Handler = GetConsoleLogDiagnostics
			};
			truncatedStringsCallback = new TruncatedStringsCallback
			{
				GetHandler = GetTruncatedStrings,
				ClearHandler = ClearTruncatedStrings
			};
			delegateFactory = new DelegateFactory();
			platformDateTimeProvider = new PlatformDateTimeProvider();
			networkManagementCallback = new NetworkManagementCallback();
			customSubWizardCallback = new CustomSubWizardCallback();
			savedDataProvider = new SavedDataProvider();
			UniqueIdentifiers uniqueIdentifiers = GetUniqueIdentifiers();
			string sLanguageID = (LanguageID = GetLanguageId());
			int userGeoID = NativeMethods.GetUserGeoID(NativeMethods.GeoClass.Nation);
			int userDefaultLCID = NativeMethods.GetUserDefaultLCID();
			StringBuilder stringBuilder = new StringBuilder(100);
			NativeMethods.GetGeoInfo(userGeoID, 4, stringBuilder, stringBuilder.Capacity, userDefaultLCID);
			string sDefaultCountryCode = stringBuilder.ToString().Trim();
			parameters = new SCLibParameters
			{
				m_sJFFSRoot = FileSystem.JffsDirectory,
				m_sAnacapaConfFilePath = FileSystem.AnacapaConfigFile,
				m_sResourcesPath = FileSystem.ResourcesDirectory,
				m_sDownloadedResourcesPath = FileSystem.DownloadResourcesDirectory,
				m_sLanguageID = sLanguageID,
				m_sDefaultCountryCode = sDefaultCountryCode,
				m_sOSVersion = OSVersionHelper.VersionString,
				m_sHostDeviceID = uniqueIdentifiers.MachineIdentifier,
				m_sHostMACAddress = uniqueIdentifiers.MacAddress,
				m_sHostModel = HostModel,
				m_bRequiresUnicastAlive = false,
				m_pLoggerCB = logCallback,
				m_pAssertionFailureCB = assertionFailureCallback,
				m_pCallUIThreadCB = uiThreadCallback,
				m_UIParams = new SCUserInterfaceParameters
				{
					m_browseTextStyle = SCUserInterfaceParameters.BrowseTextStyle.SCUI_BTS_LONG,
					m_formFactor = SCUserInterfaceParameters.FormFactorType.SCUI_FF_DESKTOP,
					m_screenDensity = SCUserInterfaceParameters.ScreenDensityType.SCUI_DENSITY_HIGH,
					m_browseSearchMode = SCUserInterfaceParameters.BrowseSearchMode.SCUI_HAS_OWN_SEARCH_UI,
					m_screenHeight = Screen.PrimaryScreen.Bounds.Height,
					m_screenWidth = Screen.PrimaryScreen.Bounds.Width
				},
				m_pDiagnosticCallback = diagnosticExtraInfoCallback,
				m_pDiagnosticConsoleLogCallback = diagnosticConsoleLogCallback,
				m_nAnacapaPortSearchAttempts = 10,
				m_eAnacapaLogEnable = SCLibParameters.AnacapaStdLogEnable.AnacapaLogEnable,
				m_bAnacapaUseConfFile = false,
				m_pTruncatedStringsCallback = truncatedStringsCallback,
				m_pCustomSubWizardCallback = customSubWizardCallback,
				m_bEnableSearchManager = true,
				m_sDeveloperOptions = "PlayModel",
				m_pSCLibDelegateFactory = delegateFactory,
				m_pPlatformDateTimeProvider = platformDateTimeProvider,
				m_pSavedDataProvider = savedDataProvider,
				m_sInteropUriScheme = "sonos://",
				m_sHostHardware = "Windows"
			};
			Screen[] allScreens = Screen.AllScreens;
			foreach (Screen val in allScreens)
			{
				parameters.m_UIParams.addScreenResolution(val.Bounds.Width.ToString(), val.Bounds.Height.ToString());
			}
			AddDiagnosticCommands(parameters);
			Library = sclib.SCLibInit(parameters);
			if (Library == null)
			{
				throw new LibraryException("SCLibInit failed. No library was returned.");
			}
			Library.setNetworkManagementDelegate(networkManagementCallback);
			ActionFactory = new ActionFactory();
			Library.setActionFactory(ActionFactory);
			Library.createServiceAppInteropManager(new ServiceAppInterop());
			LogManager.Logger.LogInformation("Setting stack trace capture callback");
			Library.setStackTraceCaptureCallback(new StackTraceCaptureDelegate());
			StartRecycling();
			Library.QueryInterface<SCIDebug>()?.setMaxListenerCountThreshold(100);
			if (ActionFactory == null)
			{
				throw new LibraryException("Action factory not created.");
			}
			if (Library == null)
			{
				throw new LibraryException("Library not created.");
			}
		}
		catch (TypeInitializationException ex)
		{
			LogManager.Logger.LogFatalError(ex, "Error loading types in SCLib.");
			throw;
		}
	}

	public void Terminate()
	{
		try
		{
			if (Library == null)
			{
				throw new LibraryException("Terminate was called when there is no Library.");
			}
			StopRecycling();
			sclib.SCLibTerm(Library);
			FinalRecycle();
		}
		catch (TypeUnloadedException ex)
		{
			LogManager.Logger.LogFatalError(ex, "Error terminating SCLib - SCLib isn't currently loaded.");
			throw;
		}
	}

	private static UniqueIdentifiers GetUniqueIdentifiers()
	{
		Dictionary<string, string> dictionary = FileSystem.LoadUIData();
		Dictionary<string, string> dictionary2 = FileSystem.LoadPermanentData();
		bool flag = false;
		if (!dictionary2.TryGetValue("MachineIdentifier", out var value) || string.IsNullOrWhiteSpace(value))
		{
			if (!dictionary.TryGetValue("MachineIdentifier", out value) || string.IsNullOrWhiteSpace(value))
			{
				value = (dictionary["MachineIdentifier"] = Guid.NewGuid().ToString());
			}
			flag = true;
		}
		if (!dictionary2.TryGetValue("MACAddress", out var value2) || !IsMacAddressValid(value2))
		{
			if (!dictionary.TryGetValue("MACAddress", out value2) || !IsMacAddressValid(value2))
			{
				value2 = NetworkMonitor.GetMacAddress();
				if (!IsMacAddressValid(value2))
				{
					value2 = "00:00:00:00:00:00";
				}
				dictionary["MACAddress"] = value2;
			}
			flag = true;
		}
		if (flag)
		{
			FileSystem.SavePermanentData(dictionary);
		}
		return new UniqueIdentifiers
		{
			MachineIdentifier = value,
			MacAddress = value2
		};
	}

	private static bool IsMacAddressValid(string macAddress)
	{
		if (!string.IsNullOrWhiteSpace(macAddress) && macAddress != "00:00:00:00:00:00")
		{
			return sclib.SCLibIsValidMACAddress(macAddress);
		}
		return false;
	}

	private string GetLanguageId()
	{
		string text = null;
		string text2 = null;
		string text3 = CultureInfo.CurrentCulture.ToString();
		SCIStringArray sCIStringArray = sclib.SCLibGetSupportedLanguageIDs();
		for (uint num = 0u; num < sCIStringArray.size(); num++)
		{
			string at = sCIStringArray.getAt(num);
			if (text3 == at)
			{
				text = at;
				break;
			}
			if (string.Compare(at, 0, text3, 0, 2, ignoreCase: true, CultureInfo.InvariantCulture) == 0)
			{
				text2 = at;
			}
		}
		return text ?? text2 ?? "en-US";
	}

	private void AddDiagnosticCommands(SCLibParameters parameters)
	{
		SCIStringArray diagnosticFiles = parameters.getDiagnosticFiles();
		diagnosticFiles.append(FileSystem.AnacapaConfigFile);
		diagnosticFiles.append(FileSystem.ManagedSharesFile);
		SCIStringArray diagnosticCommandNames = parameters.getDiagnosticCommandNames();
		SCIStringArray diagnosticCommands = parameters.getDiagnosticCommands();
		Action<string, string> action = delegate(string commandName, string command)
		{
			diagnosticCommandNames.append(commandName);
			diagnosticCommands.append(command);
		};
		action("/tasks", "tasklist");
		action("/services", "tasklist /svc");
		action("/ipconfig", "ipconfig /all");
		action("/netstat", "netstat -an");
		action("/arp", "arp -a");
		action("/shares", "net share");
		action("/shares_ex", "REG QUERY HKLM\\SYSTEM\\CurrentControlSet\\Services\\LanmanServer\\Shares");
		action("/users", "net user");
		action("/sonos_reg_hklm", "REG QUERY HKLM\\SOFTWARE\\Sonos /s");
		action("/sonos_reg_hkcu", "REG QUERY HKCU\\SOFTWARE\\Sonos /s");
		action("/nbstat_n", "nbtstat -n");
		action("/nbstat_c", "nbtstat -c");
		action("/nbstat_r", "nbtstat -r");
		action("/route_print", "route print");
		action("/net_file", "net file");
		action("/net_statistics_server", "net statistics server");
		action("/net_statistics_workstation", "net statistics workstation");
		action("/net_user", "net user Sonos");
		action("/net_localgroup", "net localgroup");
		action("/net_start", "net start");
		action("/nslookup_radiotime", "nslookup legato.radiotime.com.");
		action("/nslookup_sonos", "nslookup www.sonos.com.");
		action("/nslookup_moapi", "nslookup moapi.sonos.com.");
		action("/nslookup_sysapisonos", "nslookup system-api.sonos.com.");
		action("/nslookup_updatesonos", "nslookup update.sonos.com.");
	}

	private void CallUIThread()
	{
		UIThreadDispatcher.BeginInvoke((Delegate)(Action)delegate
		{
			if (Library != null)
			{
				Library.SCLibUIThreadCallback();
			}
			else
			{
				LogManager.Logger.LogInformation("CallUIThread dispatch-invoked call has been called after the library was cleaned up.");
			}
		}, (object[])null);
	}

	private void Log(string module, int level, string message)
	{
		switch (level)
		{
		case 0:
			LogManager.Logger.LogFatalError(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		case 1:
			LogManager.Logger.LogError(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		case 2:
			LogManager.Logger.LogWarning(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		case 3:
			LogManager.Logger.LogInformation(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		case 4:
			LogManager.Logger.LogDebug(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		default:
			LogManager.Logger.LogTrace(delegate(FormatMessageHandler m)
			{
				m("@Module:{0} @Message:{1}", module, message);
			});
			break;
		}
	}

	private void AssertionFailure(string fileName, int lineNumber, string assertionText)
	{
		LogManager.Logger.LogFatalError(delegate(FormatMessageHandler m)
		{
			m("ASSERT: {0}:{1}: {2}", fileName, lineNumber, assertionText);
		});
		Terminate();
		throw new LibraryException("Killing the app due to an assertion failure.");
	}

	private string GetExtraInfoDiagnostics()
	{
		string ssid = "";
		string bssid = "";
		bool networkOpen = false;
		SonosAdminProxy.getInstance().runSafely(delegate(SonosAdminGlue admin)
		{
			admin.WiFiDoesAdapterExist(out ssid, out bssid, out networkOpen);
		});
		StringBuilder stringBuilder = new StringBuilder();
		stringBuilder.Append("<WirelessInfo>");
		stringBuilder.Append(string.Format("<SSID><![CDATA[{0}]]></SSID>\n", string.IsNullOrEmpty(ssid) ? "SSID is Unknown" : $"SSID = {ssid}"));
		stringBuilder.Append(string.Format("<BSSID><![CDATA[{0}]]></BSSID>", string.IsNullOrEmpty(bssid) ? "BSSID is Unknown" : $"BSSID = {bssid}"));
		stringBuilder.Append("</WirelessInfo>\n");
		return stringBuilder.ToString();
	}

	private string GetConsoleLogDiagnostics()
	{
		StringBuilder stringBuilder = new StringBuilder();
		stringBuilder.Append("<Logcat><![CDATA[");
		LogManager.ReadLogForDiagnostics(stringBuilder);
		stringBuilder.Append("]]></Logcat>\n");
		stringBuilder.Append("<UserErrorLog><![CDATA[");
		stringBuilder.Append(Listener.Singleton.RenderErrorLogContent());
		stringBuilder.Append("]]></UserErrorLog>\n");
		return stringBuilder.ToString();
	}

	private string GetTruncatedStrings()
	{
		//IL_0011: Unknown result type (might be due to invalid IL or missing references)
		//IL_0017: Expected O, but got Unknown
		if (TextBlockTruncationService.IsTruncationDetectionEnabled)
		{
			XmlSerializer val = new XmlSerializer(typeof(AutomationData));
			using MemoryStream memoryStream = new MemoryStream();
			val.Serialize((Stream)memoryStream, (object)automationData);
			return Encoding.UTF8.GetString(memoryStream.GetBuffer());
		}
		return null;
	}

	private void ClearTruncatedStrings()
	{
		if (TextBlockTruncationService.IsTruncationDetectionEnabled)
		{
			automationData.Truncations.List.Clear();
		}
	}

	public void AddTruncatedString(TextBlockTruncation truncation)
	{
		if (TextBlockTruncationService.IsTruncationDetectionEnabled)
		{
			LogManager.Logger.LogDebug(delegate(FormatMessageHandler m)
			{
				m("TRUNCATION {3} - {4}!\r\n\tname: {0} uid: {1}\r\n\ttext: {2}", truncation.Name, truncation.Uid, truncation.Data, (truncation is TextBlockTruncationError) ? "ERROR" : "WARNING", truncation.Reason);
			});
			automationData.Truncations.List.Add(truncation);
		}
	}

	public void SuspendNetworking()
	{
		UIThreadDispatcher.BeginInvoke((Delegate)(Action)delegate
		{
			if (NetworkManagement != null)
			{
				NetworkManagement.suspendNetworking();
			}
		}, (DispatcherPriority)4, (object[])null);
	}

	public void ResumeNetworking()
	{
		UIThreadDispatcher.BeginInvoke((Delegate)(Action)delegate
		{
			if (NetworkManagement != null)
			{
				NetworkManagement.resumeNetworking();
				Listener.Singleton.RefreshHouseholdStateForLC();
			}
		}, (DispatcherPriority)4, (object[])null);
	}

	public void NetworkChanged()
	{
		UIThreadDispatcher.BeginInvoke((Delegate)(Action)delegate
		{
			if (NetworkManagement != null)
			{
				string ssid = "";
				string bssid = "";
				bool networkOpen = false;
				SonosAdminProxy.getInstance().runSafely(delegate(SonosAdminGlue admin)
				{
					admin.WiFiDoesAdapterExist(out ssid, out bssid, out networkOpen);
				});
				NetworkManagement.networkChanged(ssid, GetLocalIPAddress(), bssid);
			}
		}, (DispatcherPriority)4, (object[])null);
	}

	public string GetLocalIPAddress()
	{
		try
		{
			IPAddress[] addressList = Dns.GetHostEntry(Dns.GetHostName()).AddressList;
			foreach (IPAddress iPAddress in addressList)
			{
				if (iPAddress != null && iPAddress.AddressFamily == AddressFamily.InterNetwork && !IPAddress.IsLoopback(iPAddress))
				{
					return iPAddress.ToString();
				}
			}
		}
		catch (SocketException ex)
		{
			LogManager.Logger.LogError(ex, "Error getting Local IP Address, Socket Exception");
		}
		catch (ArgumentException ex2)
		{
			LogManager.Logger.LogError(ex2, "Error getting Local IP Address, Argument Exception.");
		}
		return "";
	}

	private void StartRecycling()
	{
		UIThreadDispatcher.BeginInvoke((Delegate)(Action)delegate
		{
			recycling = true;
			ComponentDispatcher.ThreadIdle += ComponentDispatcher_ThreadIdle;
		}, (DispatcherPriority)2, (object[])null);
	}

	private void ComponentDispatcher_ThreadIdle(object sender, EventArgs e)
	{
		if (UIThreadDispatcher == null)
		{
			throw new LibraryException("The thread dispatcher cannot be set to null.");
		}
		if (!UIThreadDispatcher.CheckAccess())
		{
			throw new LibraryException("The current dispatcher is not the UI thread.");
		}
		if (recycling)
		{
			NativeObjectManager.CleanupObjects(20);
		}
	}

	private void StopRecycling()
	{
		if (UIThreadDispatcher == null || !UIThreadDispatcher.CheckAccess())
		{
			throw new LibraryException("Must call StopRecycling from the UI thread.");
		}
		recycling = false;
		ComponentDispatcher.ThreadIdle -= ComponentDispatcher_ThreadIdle;
		NativeObjectManager.CleanupAllRemainingObjectsExceptFor(new NativeObjectWrapper[13]
		{
			logCallback, assertionFailureCallback, uiThreadCallback, diagnosticExtraInfoCallback, diagnosticConsoleLogCallback, truncatedStringsCallback, delegateFactory, platformDateTimeProvider, savedDataProvider, networkManagementCallback,
			customSubWizardCallback, ActionFactory, Library
		});
	}

	private void FinalRecycle()
	{
		if (Library != null)
		{
			Library.Dispose();
		}
		Library = null;
		if (logCallback != null)
		{
			logCallback.Dispose();
		}
		logCallback = null;
		if (assertionFailureCallback != null)
		{
			assertionFailureCallback.Dispose();
		}
		assertionFailureCallback = null;
		if (uiThreadCallback != null)
		{
			uiThreadCallback.Dispose();
		}
		uiThreadCallback = null;
		if (diagnosticExtraInfoCallback != null)
		{
			diagnosticExtraInfoCallback.Dispose();
		}
		diagnosticExtraInfoCallback = null;
		if (diagnosticConsoleLogCallback != null)
		{
			diagnosticConsoleLogCallback.Dispose();
		}
		diagnosticConsoleLogCallback = null;
		if (truncatedStringsCallback != null)
		{
			truncatedStringsCallback.Dispose();
		}
		truncatedStringsCallback = null;
		if (delegateFactory != null)
		{
			delegateFactory.Dispose();
		}
		delegateFactory = null;
		if (platformDateTimeProvider != null)
		{
			platformDateTimeProvider.Dispose();
		}
		platformDateTimeProvider = null;
		if (savedDataProvider != null)
		{
			savedDataProvider.Dispose();
		}
		savedDataProvider = null;
		if (networkManagementCallback != null)
		{
			networkManagementCallback.Dispose();
		}
		networkManagementCallback = null;
		if (customSubWizardCallback != null)
		{
			customSubWizardCallback.Dispose();
		}
		customSubWizardCallback = null;
		if (ActionFactory != null)
		{
			ActionFactory.Dispose();
		}
		ActionFactory = null;
	}
}
