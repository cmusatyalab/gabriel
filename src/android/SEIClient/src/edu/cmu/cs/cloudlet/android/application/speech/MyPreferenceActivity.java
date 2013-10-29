package edu.cmu.cs.cloudlet.android.application.speech;

import edu.cmu.cs.cloudlet.android.R;
import android.content.SharedPreferences;
import android.content.SharedPreferences.OnSharedPreferenceChangeListener;
import android.os.Bundle;
import android.preference.EditTextPreference;
import android.preference.PreferenceActivity;

public class MyPreferenceActivity extends PreferenceActivity implements OnSharedPreferenceChangeListener
{
	public static final String KEY_SERVER_ADDRESS_PREFERENCE = "serveraddresspreference";
	public static final String KEY_SERVER_PORT_PREFERENCE = "serverportpreference";
	
	private EditTextPreference serverAddressPreference;
	private EditTextPreference portAddressPreference;
	
	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		 addPreferencesFromResource(R.xml.preferences);
		
		 serverAddressPreference = (EditTextPreference)getPreferenceScreen().findPreference( KEY_SERVER_ADDRESS_PREFERENCE );
		 portAddressPreference = (EditTextPreference)getPreferenceScreen().findPreference( KEY_SERVER_PORT_PREFERENCE );
		 
		 updateSharedPreferenceSummaries();
		 
		 getPreferenceScreen().getSharedPreferences().registerOnSharedPreferenceChangeListener( this );
		 
		 
	}

	@Override
	public void onSharedPreferenceChanged( SharedPreferences sharedPreferences, String key )
	{
		updateSharedPreferenceSummaries();
	}
	
	
	public void updateSharedPreferenceSummaries()
	{
		 String defa = getPreferenceScreen().getSharedPreferences().getString(KEY_SERVER_ADDRESS_PREFERENCE, "127.0.0.1");
		 serverAddressPreference.setSummary(defa);
		 
		 String defb = getPreferenceScreen().getSharedPreferences().getString(KEY_SERVER_PORT_PREFERENCE, "6789");
		 portAddressPreference.setSummary(defb);
	}
}
