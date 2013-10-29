package edu.cmu.cs.cloudlet.android.application.face.batch;

import android.content.Intent;
import android.content.SharedPreferences;
import android.content.SharedPreferences.OnSharedPreferenceChangeListener;
import android.os.Bundle;
import android.preference.EditTextPreference;
import android.preference.Preference;
import android.preference.Preference.OnPreferenceClickListener;
import android.preference.PreferenceManager;
import android.text.InputType;
import edu.cmu.cs.cloudlet.android.R;
import edu.cmu.cs.cloudlet.android.application.speech.FileBrowserActivity;

public class FacePreferenceActivity extends android.preference.PreferenceActivity implements OnSharedPreferenceChangeListener, OnPreferenceClickListener
{

	public static final int FILE_BROWSER_REQUEST_CODE = 21212;

	private EditTextPreference ipaddressPref;
	private EditTextPreference portnumberPref;
	private Preference directoryPref;

	@Override
	protected void onCreate(Bundle savedInstanceState) 
	{
		super.onCreate(savedInstanceState);
		addPreferencesFromResource(R.xml.face_preferences);

		ipaddressPref = (EditTextPreference)getPreferenceScreen()
		.findPreference( getString(R.string.pref_ipaddress) );

		portnumberPref = (EditTextPreference)getPreferenceScreen()
		.findPreference( getString( R.string.pref_portnumber));
		portnumberPref.getEditText().setInputType(InputType.TYPE_CLASS_NUMBER);

		directoryPref = (Preference)getPreferenceScreen()
		.findPreference(getString(R.string.pref_directory));

		directoryPref.setOnPreferenceClickListener( this );

		updatePreferneces();

		getPreferenceScreen()
		.getSharedPreferences()
		.registerOnSharedPreferenceChangeListener( this );

	}

	public void updatePreferneces()
	{
		String ipAddress = getPreferenceScreen()
		.getSharedPreferences()
		.getString( getString(R.string.pref_ipaddress), getString(R.string.default_ipaddress));
		ipaddressPref.setSummary(ipAddress);

		String portNumber = getPreferenceScreen()
		.getSharedPreferences()
		.getString(getString(R.string.pref_portnumber), getString(R.string.default_portnumber));
		portnumberPref.setSummary(portNumber);

		String directory = getPreferenceScreen()
		.getSharedPreferences()
		.getString(getString(R.string.pref_directory), getString(R.string.default_directory));
		directoryPref.setSummary(directory);
	}

	@Override
	public void onSharedPreferenceChanged(SharedPreferences sharedPreferences, String key) 
	{
		updatePreferneces();	
	}


	@Override
	public boolean onPreferenceClick(Preference preference) 
	{
		if( preference.equals( directoryPref ) )
		{
			Intent intent = new Intent( FacePreferenceActivity.this, FileBrowserActivity.class );
			intent.putExtra( getString( R.string.intent_key_directory), directoryPref.getSummary().toString() );
			startActivityForResult( intent, FILE_BROWSER_REQUEST_CODE );
			return true;
		}
		return false;
	}

	@Override
	protected void onActivityResult(int requestCode, int resultCode, Intent data) 
	{
		switch( requestCode )
		{
		case FILE_BROWSER_REQUEST_CODE:
			switch( resultCode )
			{
			case RESULT_OK:
				String dir = data.getStringExtra( getString(R.string.intent_key_directory ) );
				SharedPreferences.Editor editor = PreferenceManager.getDefaultSharedPreferences( FacePreferenceActivity.this ).edit();
				editor.putString( getString( R.string.pref_directory), dir );
				editor.apply();
				updatePreferneces();
				break;
			default:
				break;
			}
			break;
		default:
			break;
		}
		super.onActivityResult(requestCode, resultCode, data);
	}

}
