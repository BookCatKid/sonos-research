using Sonos.SCLib.Interop;

namespace Sonos.Controller.Desktop.SCLib;

internal class SavedDataProvider : SCISavedDataProviderSwigBase
{
	private SavedData data = new SavedData("sonos_application_cache.config");

	public override bool getBoolValue(string key)
	{
		return data.ReadBool(key);
	}

	public override void setBoolValue(string key, bool value)
	{
		data.WriteBool(key, value);
		data.Save();
	}

	public override void registerDefaultBoolValue(string key, bool value)
	{
		data.SetDefaultValue(key, value);
	}

	public override int getIntegerValue(string key)
	{
		return data.ReadInt(key);
	}

	public override void setIntegerValue(string key, int value)
	{
		data.WriteInt(key, value);
		data.Save();
	}

	public override void registerDefaultIntegerValue(string key, int value)
	{
		data.SetDefaultValue(key, value);
	}

	public override double getDoubleValue(string key)
	{
		return data.ReadDouble(key);
	}

	public override void setDoubleValue(string key, double value)
	{
		data.WriteDouble(key, value);
		data.Save();
	}

	public override void registerDefaultDoubleValue(string key, double value)
	{
		data.SetDefaultValue(key, value);
	}

	public override string getStringValue(string key)
	{
		return data.ReadString(key);
	}

	public override void setStringValue(string key, string value)
	{
		data.WriteString(key, value);
		data.Save();
	}

	public override void registerDefaultStringValue(string key, string value)
	{
		data.SetDefaultValue(key, value);
	}

	public override void remove(string key)
	{
		data.Remove(key);
		data.Save();
	}
}
