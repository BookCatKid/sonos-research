using System;
using System.Runtime.InteropServices;
using Sonos.SCLib.Interop.Utils;

namespace Sonos.SCLib.Interop;

public class SCIUserAccount : SCIObj
{
	public enum VerificationStatusLevel
	{
		VERIFICATION_STATUS_LEVEL_VERIFIED,
		VERIFICATION_STATUS_LEVEL_PENDING,
		VERIFICATION_STATUS_LEVEL_NOT_VERIFIED
	}

	public enum ReleaseProgramType
	{
		RELEASE_PROGRAM_LEVEL_ALPHA,
		RELEASE_PROGRAM_LEVEL_PUBLIC_BETA,
		RELEASE_PROGRAM_LEVEL_PRIVATE_BETA,
		RELEASE_PROGRAM_LEVEL_DEFAULT
	}

	public enum Role
	{
		OWNER,
		ADMIN,
		UNKNOWN
	}

	private HandleRef swigCPtr;

	internal SCIUserAccount(IntPtr cPtr, bool cMemoryOwn)
		: this(cPtr, cMemoryOwn, sclibPINVOKE.delete_SCIUserAccount)
	{
	}

	internal SCIUserAccount(IntPtr cPtr, bool cMemoryOwn, NativeObjectManager.DestructorDelegate dtorDelegate)
		: base(sclibPINVOKE.SCIUserAccount_SWIGUpcast(cPtr), cMemoryOwn, dtorDelegate)
	{
		swigCPtr = new HandleRef(this, cPtr);
	}

	internal static HandleRef getCPtr(SCIUserAccount obj)
	{
		return obj?.swigCPtr ?? new HandleRef(null, IntPtr.Zero);
	}

	public override IntPtr getNativeCPtr()
	{
		return swigCPtr.Handle;
	}

	~SCIUserAccount()
	{
		MarkObjectToBeFreed();
	}

	protected override void MarkObjectToBeFreed()
	{
		base.MarkObjectToBeFreed();
	}

	public virtual ReleaseProgramType getReleaseProgramType()
	{
		return (ReleaseProgramType)sclibPINVOKE.SCIUserAccount_getReleaseProgramType(swigCPtr);
	}

	public virtual VerificationStatusLevel getVerificationStatus()
	{
		return (VerificationStatusLevel)sclibPINVOKE.SCIUserAccount_getVerificationStatus(swigCPtr);
	}

	public virtual string getId()
	{
		return sclibPINVOKE.SCIUserAccount_getId(swigCPtr);
	}

	public virtual string getEmail()
	{
		return sclibPINVOKE.SCIUserAccount_getEmail(swigCPtr);
	}

	public virtual void signOut()
	{
		sclibPINVOKE.SCIUserAccount_signOut(swigCPtr);
	}

	public virtual bool refreshUserProfileInfo()
	{
		return sclibPINVOKE.SCIUserAccount_refreshUserProfileInfo(swigCPtr);
	}

	public virtual void showVerificationStatus()
	{
		sclibPINVOKE.SCIUserAccount_showVerificationStatus(swigCPtr);
	}

	public virtual void hideVerificationStatus()
	{
		sclibPINVOKE.SCIUserAccount_hideVerificationStatus(swigCPtr);
	}

	public virtual void subscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCIUserAccount_subscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}

	public virtual void unsubscribe(SCIEventSink pEventSink)
	{
		sclibPINVOKE.SCIUserAccount_unsubscribe(swigCPtr, SCIEventSink.getCPtr(pEventSink));
	}
}
