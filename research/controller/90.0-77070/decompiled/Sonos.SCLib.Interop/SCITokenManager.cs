using System;
using System.Runtime.InteropServices;
using Sonos.SCLib.Interop.Utils;

namespace Sonos.SCLib.Interop;

public class SCITokenManager : SCIObj
{
	public enum SCTokenPurpose
	{
		DEFAULT_USER_PURPOSE,
		PARENTAL_CONTROLS_PURPOSE,
		REGISTER_PLAYER_PURPOSE,
		TRANSFER_PURPOSE,
		HH_CONFIG_PURPOSE,
		HH_CONFIG_ADMIN_PURPOSE,
		HISTORY_PURPOSE,
		CONNECTED_PARTNERS_PURPOSE,
		CHANGE_EMAIL_PURPOSE,
		RECYCLE_DEVICES_PURPOSE,
		DEVICE_REMOVAL_PURPOSE,
		LIFECYCLE_PURPOSE,
		ACCEPT_SR_TOS_PURPOSE,
		PURPOSE_MAX
	}

	private HandleRef swigCPtr;

	internal SCITokenManager(IntPtr cPtr, bool cMemoryOwn)
		: this(cPtr, cMemoryOwn, sclibPINVOKE.delete_SCITokenManager)
	{
	}

	internal SCITokenManager(IntPtr cPtr, bool cMemoryOwn, NativeObjectManager.DestructorDelegate dtorDelegate)
		: base(sclibPINVOKE.SCITokenManager_SWIGUpcast(cPtr), cMemoryOwn, dtorDelegate)
	{
		swigCPtr = new HandleRef(this, cPtr);
	}

	internal static HandleRef getCPtr(SCITokenManager obj)
	{
		return obj?.swigCPtr ?? new HandleRef(null, IntPtr.Zero);
	}

	public override IntPtr getNativeCPtr()
	{
		return swigCPtr.Handle;
	}

	~SCITokenManager()
	{
		MarkObjectToBeFreed();
	}

	protected override void MarkObjectToBeFreed()
	{
		base.MarkObjectToBeFreed();
	}

	public virtual void subscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCITokenManager_subscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual void unsubscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCITokenManager_unsubscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}
}
