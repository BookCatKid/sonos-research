using System;
using System.Runtime.InteropServices;
using Sonos.SCLib.Interop.Utils;

namespace Sonos.SCLib.Interop;

public class SCISystem : SCIObj
{
	private HandleRef swigCPtr;

	internal SCISystem(IntPtr cPtr, bool cMemoryOwn)
		: this(cPtr, cMemoryOwn, sclibPINVOKE.delete_SCISystem)
	{
	}

	internal SCISystem(IntPtr cPtr, bool cMemoryOwn, NativeObjectManager.DestructorDelegate dtorDelegate)
		: base(sclibPINVOKE.SCISystem_SWIGUpcast(cPtr), cMemoryOwn, dtorDelegate)
	{
		swigCPtr = new HandleRef(this, cPtr);
	}

	internal static HandleRef getCPtr(SCISystem obj)
	{
		return obj?.swigCPtr ?? new HandleRef(null, IntPtr.Zero);
	}

	public override IntPtr getNativeCPtr()
	{
		return swigCPtr.Handle;
	}

	~SCISystem()
	{
		MarkObjectToBeFreed();
	}

	protected override void MarkObjectToBeFreed()
	{
		base.MarkObjectToBeFreed();
	}

	public static SCISystem getSingleton()
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_getSingleton();
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCISystem(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public static SCISystem getInterface(SCILibrary pIfcHolder)
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_getInterface(SCILibrary.getCPtr(pIfcHolder));
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCISystem(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool factoryResetConfigFiles(bool fullReset)
	{
		return sclibPINVOKE.SCISystem_factoryResetConfigFiles__SWIG_0(swigCPtr, fullReset);
	}

	public virtual bool factoryResetConfigFiles()
	{
		return sclibPINVOKE.SCISystem_factoryResetConfigFiles__SWIG_1(swigCPtr);
	}

	public virtual bool isFactoryReset()
	{
		return sclibPINVOKE.SCISystem_isFactoryReset(swigCPtr);
	}

	public virtual void resynchronizeHousehold()
	{
		sclibPINVOKE.SCISystem_resynchronizeHousehold(swigCPtr);
	}

	public virtual bool canForgetCurrentHousehold()
	{
		return sclibPINVOKE.SCISystem_canForgetCurrentHousehold(swigCPtr);
	}

	public virtual void subscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCISystem_subscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual void unsubscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCISystem_unsubscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual bool isRunningBackgroundOperations()
	{
		return sclibPINVOKE.SCISystem_isRunningBackgroundOperations(swigCPtr);
	}

	public virtual SCIWizard createLegacyJoinExistingWizard()
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_createLegacyJoinExistingWizard(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIWizard(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIArtworkCacheManager getArtworkCacheManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_getArtworkCacheManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIArtworkCacheManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual void setNetstartListener(SCINetstartListener listener)
	{
		sclibPINVOKE.SCISystem_setNetstartListener(swigCPtr, SCINetstartListener.getCPtr(listener));
	}

	public virtual SCINetstartListener getNetstartListener()
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_getNetstartListener(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCINetstartListener(intPtr, cMemoryOwn: false);
		}
		return null;
	}

	public virtual string getCopyright()
	{
		return sclibPINVOKE.SCISystem_getCopyright(swigCPtr);
	}

	public virtual string getControllerIP()
	{
		return sclibPINVOKE.SCISystem_getControllerIP(swigCPtr);
	}

	public virtual string getCustomerID()
	{
		return sclibPINVOKE.SCISystem_getCustomerID(swigCPtr);
	}

	public virtual bool needToResumeOnlineUpdate()
	{
		return sclibPINVOKE.SCISystem_needToResumeOnlineUpdate(swigCPtr);
	}

	public virtual void cleanupOnlineUpdateFiles()
	{
		sclibPINVOKE.SCISystem_cleanupOnlineUpdateFiles(swigCPtr);
	}

	public virtual SCIEnumerator getDebugWizardActions()
	{
		IntPtr intPtr = sclibPINVOKE.SCISystem_getDebugWizardActions(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool saveHHSettingString(string sSettingName, string sSettingValue)
	{
		bool result = sclibPINVOKE.SCISystem_saveHHSettingString(swigCPtr, sSettingName, sSettingValue);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual bool removeHHSettingString(string sSettingName)
	{
		bool result = sclibPINVOKE.SCISystem_removeHHSettingString(swigCPtr, sSettingName);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual bool hasHHSettingString(string sSettingName)
	{
		bool result = sclibPINVOKE.SCISystem_hasHHSettingString(swigCPtr, sSettingName);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual string getHHSettingString(string sSettingName)
	{
		string result = sclibPINVOKE.SCISystem_getHHSettingString(swigCPtr, sSettingName);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual string getCloudEnvironment()
	{
		return sclibPINVOKE.SCISystem_getCloudEnvironment(swigCPtr);
	}

	public virtual void setCloudEnvironment(string env)
	{
		sclibPINVOKE.SCISystem_setCloudEnvironment(swigCPtr, env);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
	}

	public virtual string getHouseholdID()
	{
		return sclibPINVOKE.SCISystem_getHouseholdID(swigCPtr);
	}

	public virtual string getClientApiKey()
	{
		return sclibPINVOKE.SCISystem_getClientApiKey(swigCPtr);
	}

	public virtual void setMemoryStats(SCIPropertyBag pBag)
	{
		sclibPINVOKE.SCISystem_setMemoryStats(swigCPtr, SCIPropertyBag.getCPtr(pBag));
	}

	public static void setUpnpTunnelingEnabled(bool enabled)
	{
		sclibPINVOKE.SCISystem_setUpnpTunnelingEnabled(enabled);
	}
}
