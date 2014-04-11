package edu.cmu.cs.gabriel;

import edu.cmu.cs.gabriel.R;
import android.content.SharedPreferences;
import android.content.SharedPreferences.OnSharedPreferenceChangeListener;
import android.os.Bundle;
import android.preference.CheckBoxPreference;
import android.preference.EditTextPreference;
import android.preference.ListPreference;
import android.preference.PreferenceActivity;
import android.preference.PreferenceManager;
import android.util.Log;
import android.view.KeyEvent;


public class SettingsActivity extends PreferenceActivity implements
OnSharedPreferenceChangeListener {
	public static final String KEY_PROTOCOL_LIST = "list_protocol";
	public static final String KEY_PROXY_ENABLED = "proxy_enabled";
	public static final String KEY_PROXY_IP = "server_IP";
	public static final String KEY_PROXY_PORT = "server_port";
	
	private static SharedPreferences sharedPreferences;
	private ListPreference protocolList;
	private CheckBoxPreference proxyPref;
	private EditTextPreference proxyIP;
	private EditTextPreference proxyPort;
	
	@Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        //addPreferencesFromResource does not work with fragment-based PreferenceActivity
        addPreferencesFromResource(R.xml.preferences);
        
        protocolList = (ListPreference)getPreferenceScreen().findPreference(KEY_PROTOCOL_LIST);
        
        proxyPref = (CheckBoxPreference)getPreferenceScreen().findPreference(KEY_PROXY_ENABLED);
        
        proxyIP = (EditTextPreference)getPreferenceScreen().findPreference(KEY_PROXY_IP);
        
        proxyPort = (EditTextPreference)getPreferenceScreen().findPreference(KEY_PROXY_PORT);
        
        
        sharedPreferences = PreferenceManager.getDefaultSharedPreferences(this);
       
        
        if (sharedPreferences == null)
        	Log.e("setting", "sharedPref null");
        
        Log.v("setting", "onCreate");
    }
	
	
	@Override
	public void onSharedPreferenceChanged(SharedPreferences sharedPreferences,
			String key) {
		// TODO Auto-generated method stub
		if (key.equals(KEY_PROTOCOL_LIST)){
			sharedPreferences.edit().putString(key, protocolList.getValue()).commit();
			protocolList.setSummary(protocolList.getValue());
			
		}else if (key.equals(KEY_PROXY_ENABLED)){
			if (proxyPref.isChecked()){
				sharedPreferences.edit().putBoolean(key, proxyPref.isChecked());
				Log.v("setting","proxy enabled");
			}
		}else if (key.equals(KEY_PROXY_IP)){
			sharedPreferences.edit().putString(key, proxyIP.getText()).commit();
			proxyIP.setSummary(proxyIP.getText());
		}else if (key.equals(KEY_PROXY_PORT)){
			sharedPreferences.edit().putString(key, proxyPort.getText()).commit();
			proxyPort.setSummary(proxyPort.getText());
			Log.v("settings", "proxy port is "+proxyPort.getText());
		}
		
	}
	

	@Override
	protected void onResume(){
		 super.onResume();
		
		 Log.v("setting", "onResume");
		 
		 //set default values
		 if (protocolList != null && sharedPreferences != null){
			 protocolList.setSummary(sharedPreferences.getString(KEY_PROTOCOL_LIST, "UDP"));
		 }
		 
		 		 
		 if (proxyPref != null && sharedPreferences.getBoolean(KEY_PROXY_ENABLED, false)){
			 
			 proxyPref.setChecked(true);
		 }
		 else {
			 
			 proxyPref.setChecked(false);
			 
		 }
		 
			 
		 if (proxyIP != null)
			 proxyIP.setSummary(sharedPreferences.getString(KEY_PROXY_IP, "128.2.213.25"));
		 
		 if (proxyPort != null)
			 proxyPort.setSummary(sharedPreferences.getString(KEY_PROXY_PORT, "8080"));
		 
		 getPreferenceScreen().getSharedPreferences().registerOnSharedPreferenceChangeListener(this);
		 
	}
	
	@Override
	protected void onPause(){
		super.onPause();
		
		getPreferenceScreen().getSharedPreferences().unregisterOnSharedPreferenceChangeListener(this);
	}
	
	 @Override
	public boolean onKeyDown(int keyCode, KeyEvent event) {
	     if (keyCode == KeyEvent.KEYCODE_BACK) {
			 this.setResult(RESULT_OK);
	         finish();
	         return true;
	     }
		   
	     return super.onKeyDown(keyCode, event);
	 }
}
