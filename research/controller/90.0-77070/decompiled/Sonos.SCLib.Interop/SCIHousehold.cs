using System;
using System.Runtime.InteropServices;
using Sonos.SCLib.Interop.Utils;

namespace Sonos.SCLib.Interop;

public class SCIHousehold : SCIObj
{
	public enum ZGFilterOpt
	{
		FLT_ZG_COMPATIBLE,
		FLT_ZG_INCOMPATIBLE,
		FLT_ZG_ANY,
		FLT_ZG_ZONEMENU,
		FLT_ZG_ACTIVE,
		FLT_ZG_INACTIVE,
		FLT_ZG_QUARANTINED
	}

	public enum DevFilterOpt
	{
		FLT_DEV_COMPATIBLE_AND_VISIBLE,
		FLT_DEV_COMPATIBLE_AND_UNCONFIGURED,
		FLT_DEV_GROUPABLE,
		FLT_DEV_SETTING_MENU_ZONEPLAYERS,
		FLT_DEV_SETTING_MENU_ZONEBRIDGES,
		FLT_DEV_STEREO_PAIR_CANDIDATES,
		FLT_DEV_LINE_IN_ZONEPLAYERS,
		FLT_DEV_AIRPLAY_ZONEPLAYERS,
		FLT_DEV_SETTINGS_MENU,
		FLT_DEV_ANY,
		FLT_DEV_INCOMPATIBLE,
		FLT_DEV_COMPATIBLE,
		FLT_DEV_MODERN,
		FLT_DEV_LEGACY,
		FLT_DEV_LEGACY_BRIDGES,
		FLT_DEV_QUARANTINED,
		FLT_DEV_ROOM_DETECTION_SIGNALLER,
		FLT_DEV_IKEA_LAMP_PLAYERS
	}

	private HandleRef swigCPtr;

	internal SCIHousehold(IntPtr cPtr, bool cMemoryOwn)
		: this(cPtr, cMemoryOwn, sclibPINVOKE.delete_SCIHousehold)
	{
	}

	internal SCIHousehold(IntPtr cPtr, bool cMemoryOwn, NativeObjectManager.DestructorDelegate dtorDelegate)
		: base(sclibPINVOKE.SCIHousehold_SWIGUpcast(cPtr), cMemoryOwn, dtorDelegate)
	{
		swigCPtr = new HandleRef(this, cPtr);
	}

	internal static HandleRef getCPtr(SCIHousehold obj)
	{
		return obj?.swigCPtr ?? new HandleRef(null, IntPtr.Zero);
	}

	public override IntPtr getNativeCPtr()
	{
		return swigCPtr.Handle;
	}

	~SCIHousehold()
	{
		MarkObjectToBeFreed();
	}

	protected override void MarkObjectToBeFreed()
	{
		base.MarkObjectToBeFreed();
	}

	public virtual string getID()
	{
		return sclibPINVOKE.SCIHousehold_getID(swigCPtr);
	}

	public virtual SCIStringArray getExpectedIDs()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getExpectedIDs(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIStringArray(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool isValid()
	{
		return sclibPINVOKE.SCIHousehold_isValid(swigCPtr);
	}

	public virtual string getCustomerIDIfRegistered()
	{
		return sclibPINVOKE.SCIHousehold_getCustomerIDIfRegistered(swigCPtr);
	}

	public virtual SCIEnumerator getZoneGroups(ZGFilterOpt filterOpt)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getZoneGroups(swigCPtr, (int)filterOpt);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual uint getNumZoneGroups(ZGFilterOpt filterOpt)
	{
		return sclibPINVOKE.SCIHousehold_getNumZoneGroups(swigCPtr, (int)filterOpt);
	}

	public virtual SCIEnumerator getOfflineZoneGroups()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getOfflineZoneGroups(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool areAnyZoneGroupsUnplayable()
	{
		return sclibPINVOKE.SCIHousehold_areAnyZoneGroupsUnplayable(swigCPtr);
	}

	public virtual uint getNumOfflineZoneGroups()
	{
		return sclibPINVOKE.SCIHousehold_getNumOfflineZoneGroups(swigCPtr);
	}

	public virtual bool areAllZoneGroupsBluetooth()
	{
		return sclibPINVOKE.SCIHousehold_areAllZoneGroupsBluetooth(swigCPtr);
	}

	public virtual bool areAllZoneGroupsUnplayable()
	{
		return sclibPINVOKE.SCIHousehold_areAllZoneGroupsUnplayable(swigCPtr);
	}

	public virtual bool canAddAccounts()
	{
		return sclibPINVOKE.SCIHousehold_canAddAccounts(swigCPtr);
	}

	public virtual bool canAddSonosRadioAccount()
	{
		return sclibPINVOKE.SCIHousehold_canAddSonosRadioAccount(swigCPtr);
	}

	public virtual SCIEnumerator getDevices(DevFilterOpt filterOpt)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getDevices(swigCPtr, (int)filterOpt);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIEnumerator getOfflineDevices(bool excludeSecondaries)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getOfflineDevices__SWIG_0(swigCPtr, excludeSecondaries);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIEnumerator getOfflineDevices()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getOfflineDevices__SWIG_1(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIDevice getCurrentPrimaryDevice()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getCurrentPrimaryDevice(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIDevice(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool hasTransientOrphanedZoneGroups()
	{
		return sclibPINVOKE.SCIHousehold_hasTransientOrphanedZoneGroups(swigCPtr);
	}

	public virtual bool isConnectingToZPs()
	{
		return sclibPINVOKE.SCIHousehold_isConnectingToZPs(swigCPtr);
	}

	public virtual void setCurrentZoneGroup(string sID, string reason)
	{
		sclibPINVOKE.SCIHousehold_setCurrentZoneGroup(swigCPtr, sID, reason);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
	}

	public virtual void saveCurrentZoneGroup()
	{
		sclibPINVOKE.SCIHousehold_saveCurrentZoneGroup(swigCPtr);
	}

	public virtual SCIZoneGroup getCurrentZoneGroup()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getCurrentZoneGroup(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIZoneGroup(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool isCurrentZoneGroupStale()
	{
		return sclibPINVOKE.SCIHousehold_isCurrentZoneGroupStale(swigCPtr);
	}

	public virtual void setCurrentZoneGroupStale(bool bStale)
	{
		sclibPINVOKE.SCIHousehold_setCurrentZoneGroupStale(swigCPtr, bStale);
	}

	public virtual SCIZoneGroup lookupZoneGroup(string sID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_lookupZoneGroup(swigCPtr, sID);
		SCIZoneGroup result = ((intPtr == IntPtr.Zero) ? null : new SCIZoneGroup(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIDevice lookupDevice(string sID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_lookupDevice(swigCPtr, sID);
		SCIDevice result = ((intPtr == IntPtr.Zero) ? null : new SCIDevice(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIDevice getAssociatedDevice()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getAssociatedDevice(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIDevice(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual void performRescan(bool bManual, bool bSubmitDiag)
	{
		sclibPINVOKE.SCIHousehold_performRescan(swigCPtr, bManual, bSubmitDiag);
	}

	public virtual SCIIndexManager getIndexManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getIndexManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIIndexManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOpGetAboutSonosString createGetAboutSonosStrOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetAboutSonosStrOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOpGetAboutSonosString(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOpGetAboutSonosString createGetShortAboutSonosStrOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetShortAboutSonosStrOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOpGetAboutSonosString(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOp createPauseOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createPauseOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOp(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOp createStopOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createStopOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOp(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOp createSetPropertyOp(string sPropName, string sPropValue)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createSetPropertyOp(swigCPtr, sPropName, sPropValue);
		SCIOp result = ((intPtr == IntPtr.Zero) ? null : new SCIOp(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIOpSystemPropertyGetString createGetPropertyOp(string sPropName)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetPropertyOp(swigCPtr, sPropName);
		SCIOpSystemPropertyGetString result = ((intPtr == IntPtr.Zero) ? null : new SCIOpSystemPropertyGetString(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIOpSystemPropertyGetRDM createGetDealerModeOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetDealerModeOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOpSystemPropertyGetRDM(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOpGetUsageDataShareOption createGetUsageDataShareOptionOp()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetUsageDataShareOptionOp(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOpGetUsageDataShareOption(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIOp createSetUsageDataShareOptionOp(bool bShareUsage)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createSetUsageDataShareOptionOp(swigCPtr, bShareUsage);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIOp(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool isDataCollectionEnabled()
	{
		return sclibPINVOKE.SCIHousehold_isDataCollectionEnabled(swigCPtr);
	}

	public virtual SCIOpZoneGroupTopologyGetZoneGroupState createGetZoneGroupStateOp(string sID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createGetZoneGroupStateOp(swigCPtr, sID);
		SCIOpZoneGroupTopologyGetZoneGroupState result = ((intPtr == IntPtr.Zero) ? null : new SCIOpZoneGroupTopologyGetZoneGroupState(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createPushSCUriAction(string sSCUri, string sTitle, bool bClearStack)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createPushSCUriAction(swigCPtr, sSCUri, sTitle, bClearStack);
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createAddCustomRadioStationAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createAddCustomRadioStationAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createFactoryResetAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createFactoryResetAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createForgetHouseholdAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createForgetHouseholdAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createLegacySubmitDiagsWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createLegacySubmitDiagsWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual void subscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCIHousehold_subscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual void subscribeWithoutInitialEvent(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCIHousehold_subscribeWithoutInitialEvent(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual void unsubscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCIHousehold_unsubscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual SCIServiceDescriptorManager getServiceDescriptorManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getServiceDescriptorManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIServiceDescriptorManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIBrowseStackManager createBrowseStackWithRoot(string sRoot)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createBrowseStackWithRoot__SWIG_0(swigCPtr, sRoot);
		SCIBrowseStackManager result = ((intPtr == IntPtr.Zero) ? null : new SCIBrowseStackManager(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIBrowseStackManager createBrowseStackWithRoot(string sRoot, string sTitle)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createBrowseStackWithRoot__SWIG_1(swigCPtr, sRoot, sTitle);
		SCIBrowseStackManager result = ((intPtr == IntPtr.Zero) ? null : new SCIBrowseStackManager(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIBrowseListPresentationMap getBrowseListPresentationMap()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getBrowseListPresentationMap(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIBrowseListPresentationMap(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIEnumerator getAllSearchables()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getAllSearchables(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIEnumerator getUniversalSearchables()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getUniversalSearchables(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIEnumerator(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCISearchable getAggregatedSearchable()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getAggregatedSearchable(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCISearchable(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCISearchable lookupSearchableBySCUri(string sURI)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_lookupSearchableBySCUri(swigCPtr, sURI);
		SCISearchable result = ((intPtr == IntPtr.Zero) ? null : new SCISearchable(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCISearchable lookupSearchableBySearchSCUri(string sSearchURI)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_lookupSearchableBySearchSCUri(swigCPtr, sSearchURI);
		SCISearchable result = ((intPtr == IntPtr.Zero) ? null : new SCISearchable(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual void setTopSearchable(SCISearchable pSearchable)
	{
		sclibPINVOKE.SCIHousehold_setTopSearchable(swigCPtr, SCISearchable.getCPtr(pSearchable));
	}

	public virtual void setTopSearchableBySCUri(string sURI)
	{
		sclibPINVOKE.SCIHousehold_setTopSearchableBySCUri(swigCPtr, sURI);
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
	}

	public virtual void updateAvailableServices()
	{
		sclibPINVOKE.SCIHousehold_updateAvailableServices(swigCPtr);
	}

	public virtual bool shouldUpdateNow()
	{
		return sclibPINVOKE.SCIHousehold_shouldUpdateNow(swigCPtr);
	}

	public virtual SCIWizard createOnlineUpdateWizard(bool bFromZonesMenu)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createOnlineUpdateWizard(swigCPtr, bFromZonesMenu);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIWizard(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIWizard createOnlineUpdateIntroOnlyWizard(SCIPropertyBag pNamedParams)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createOnlineUpdateIntroOnlyWizard(swigCPtr, SCIPropertyBag.getCPtr(pNamedParams));
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIWizard(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIWizard createResumeUpdateWizard()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createResumeUpdateWizard(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIWizard(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createOnlineUpdateWizardResumeAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createOnlineUpdateWizardResumeAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createOnlineUpdateWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createOnlineUpdateWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createAddNewHouseholdWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createAddNewHouseholdWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createJoinAnotherHouseholdWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createJoinAnotherHouseholdWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createLegacyJoinHouseholdWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createLegacyJoinHouseholdWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createSwgenDowngradeProductWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createSwgenDowngradeProductWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createMusicLibrarySetupWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicLibrarySetupWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createMusicServiceAddAccountWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceAddAccountWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createMusicServiceAddSonosLabsAccountWizardAction()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceAddSonosLabsAccountWizardAction(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIActionContext(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIActionContext createMusicServiceReplaceAccountWizardAction(string sServiceAccountID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceReplaceAccountWizardAction(swigCPtr, sServiceAccountID);
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createMusicServiceReauthorizeAccountWizardAction(string sServiceAccountID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceReauthorizeAccountWizardAction(swigCPtr, sServiceAccountID);
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createMusicServiceChangePasswordWizardAction(string sServiceAccountID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceChangePasswordWizardAction(swigCPtr, sServiceAccountID);
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createMusicServiceChangeNicknameWizardAction(string sServiceAccountID)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceChangeNicknameWizardAction(swigCPtr, sServiceAccountID);
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIActionContext createMusicServiceLinkAccountDisplayWizardAction(string sUri, SCIStringArray pComponents, SCIPropertyBag pBag)
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_createMusicServiceLinkAccountDisplayWizardAction(swigCPtr, sUri, SCIStringArray.getCPtr(pComponents), SCIPropertyBag.getCPtr(pBag));
		SCIActionContext result = ((intPtr == IntPtr.Zero) ? null : new SCIActionContext(intPtr, cMemoryOwn: true));
		if (sclibPINVOKE.SWIGPendingException.Pending)
		{
			throw sclibPINVOKE.SWIGPendingException.Retrieve();
		}
		return result;
	}

	public virtual SCIAlarmManager getAlarmManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getAlarmManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIAlarmManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIDateTimeManager getDateTimeManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getDateTimeManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIDateTimeManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIShareManager getShareManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getShareManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIShareManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual SCIAreaManager getAreaManager()
	{
		IntPtr intPtr = sclibPINVOKE.SCIHousehold_getAreaManager(swigCPtr);
		if (!(intPtr == IntPtr.Zero))
		{
			return new SCIAreaManager(intPtr, cMemoryOwn: true);
		}
		return null;
	}

	public virtual bool isControllerUpdateAvailable()
	{
		return sclibPINVOKE.SCIHousehold_isControllerUpdateAvailable(swigCPtr);
	}

	public virtual string getControllerUpdateURL()
	{
		return sclibPINVOKE.SCIHousehold_getControllerUpdateURL(swigCPtr);
	}

	public virtual int getNumUncalibratedSonarZonePlayers()
	{
		return sclibPINVOKE.SCIHousehold_getNumUncalibratedSonarZonePlayers(swigCPtr);
	}

	public virtual int getNumUncalibratedSonarHTZonePlayers()
	{
		return sclibPINVOKE.SCIHousehold_getNumUncalibratedSonarHTZonePlayers(swigCPtr);
	}

	public virtual bool explicitFilteringEnabled()
	{
		return sclibPINVOKE.SCIHousehold_explicitFilteringEnabled(swigCPtr);
	}

	public virtual bool hasVoiceEnabledZP()
	{
		return sclibPINVOKE.SCIHousehold_hasVoiceEnabledZP(swigCPtr);
	}

	public virtual bool hasVoicePlayersNoneEnabled()
	{
		return sclibPINVOKE.SCIHousehold_hasVoicePlayersNoneEnabled(swigCPtr);
	}

	public virtual bool isRegisteredInVoiceSupportedRegion()
	{
		return sclibPINVOKE.SCIHousehold_isRegisteredInVoiceSupportedRegion(swigCPtr);
	}

	public virtual bool startCloudAssistedDiscovery()
	{
		return sclibPINVOKE.SCIHousehold_startCloudAssistedDiscovery(swigCPtr);
	}

	public virtual bool hasSecurelyRegisteredZPs()
	{
		return sclibPINVOKE.SCIHousehold_hasSecurelyRegisteredZPs(swigCPtr);
	}

	public virtual void keepPortableDevicesAwake()
	{
		sclibPINVOKE.SCIHousehold_keepPortableDevicesAwake(swigCPtr);
	}

	public virtual bool hhNeedsSwgenFirmwareUpdate()
	{
		return sclibPINVOKE.SCIHousehold_hhNeedsSwgenFirmwareUpdate(swigCPtr);
	}

	public virtual string getSystemName()
	{
		return sclibPINVOKE.SCIHousehold_getSystemName(swigCPtr);
	}

	public virtual bool isContentAccessEnabled()
	{
		return sclibPINVOKE.SCIHousehold_isContentAccessEnabled(swigCPtr);
	}

	public virtual void setMemoryStats(SCIPropertyBag pBag)
	{
		sclibPINVOKE.SCIHousehold_setMemoryStats(swigCPtr, SCIPropertyBag.getCPtr(pBag));
	}
}
