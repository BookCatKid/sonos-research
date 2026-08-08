using System;
using System.Collections.Generic;
using System.Configuration;
using System.IO;

namespace Sonos.Controller.Desktop.SCLib;

public class SavedData
{
	private string dataFileName;

	private Configuration config;

	private Dictionary<string, string> defaultStringValues;

	private Dictionary<string, bool> defaultBoolValues;

	private Dictionary<string, int> defaultIntValues;

	private Dictionary<string, double> defaultDoubleValues;

	public SavedData(string dataFileName)
	{
		//IL_0039: Unknown result type (might be due to invalid IL or missing references)
		//IL_003f: Expected O, but got Unknown
		defaultStringValues = new Dictionary<string, string>();
		defaultBoolValues = new Dictionary<string, bool>();
		defaultIntValues = new Dictionary<string, int>();
		defaultDoubleValues = new Dictionary<string, double>();
		this.dataFileName = dataFileName;
		ExeConfigurationFileMap val = new ExeConfigurationFileMap();
		val.ExeConfigFilename = getConfigPath(dataFileName);
		try
		{
			config = ConfigurationManager.OpenMappedExeConfiguration(val, (ConfigurationUserLevel)0);
		}
		catch (ConfigurationErrorsException)
		{
		}
	}

	public void WriteString(string key, string value)
	{
		//IL_0048: Unknown result type (might be due to invalid IL or missing references)
		//IL_0052: Expected O, but got Unknown
		if (config != null)
		{
			if (config.AppSettings.Settings[key] != null)
			{
				config.AppSettings.Settings.Remove(key);
			}
			config.AppSettings.Settings.Add(new KeyValueConfigurationElement(key, value));
		}
	}

	public string ReadString(string key)
	{
		if (config != null && config.AppSettings.Settings[key] != null)
		{
			return config.AppSettings.Settings[key].Value;
		}
		if (!defaultStringValues.TryGetValue(key, out var value))
		{
			return null;
		}
		return value;
	}

	public void WriteBool(string key, bool value)
	{
		WriteString(key, value.ToString());
	}

	public bool ReadBool(string key)
	{
		string text = ReadString(key);
		if (text != null && bool.TryParse(text, out var result))
		{
			return result;
		}
		if (!defaultBoolValues.TryGetValue(key, out var value))
		{
			return false;
		}
		return value;
	}

	public void WriteInt(string key, int value)
	{
		WriteString(key, value.ToString());
	}

	public int ReadInt(string key)
	{
		string text = ReadString(key);
		if (text != null && int.TryParse(text, out var result))
		{
			return result;
		}
		if (!defaultIntValues.TryGetValue(key, out var value))
		{
			return 0;
		}
		return value;
	}

	public void WriteDouble(string key, double value)
	{
		WriteString(key, value.ToString());
	}

	public double ReadDouble(string key)
	{
		string text = ReadString(key);
		if (text != null && double.TryParse(text, out var result))
		{
			return result;
		}
		if (!defaultDoubleValues.TryGetValue(key, out var value))
		{
			return 0.0;
		}
		return value;
	}

	public void SetDefaultValue(string key, string value)
	{
		defaultStringValues[key] = value;
	}

	public void SetDefaultValue(string key, bool value)
	{
		defaultBoolValues[key] = value;
	}

	public void SetDefaultValue(string key, int value)
	{
		defaultIntValues[key] = value;
	}

	public void SetDefaultValue(string key, double value)
	{
		defaultDoubleValues[key] = value;
	}

	public void Remove(string key)
	{
		config.AppSettings.Settings.Remove(key);
	}

	public void Clear()
	{
		config.AppSettings.Settings.Clear();
	}

	public void Save()
	{
		try
		{
			config.Save((ConfigurationSaveMode)0);
		}
		catch (ConfigurationErrorsException)
		{
		}
	}

	private string getConfigPath(string filename)
	{
		return Path.Combine(Path.Combine(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "SonosV2,_Inc"), "runtime"), filename);
	}
}
