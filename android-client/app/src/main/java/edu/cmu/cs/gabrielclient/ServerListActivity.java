// Copyright 2018 Carnegie Mellon University
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package edu.cmu.cs.gabrielclient;
import static edu.cmu.cs.gabriel.client.Util.ValidateEndpoint;

import android.app.AlertDialog;
import android.content.SharedPreferences;
import androidx.appcompat.app.AppCompatActivity;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.Log;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.widget.SeekBar;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.Switch;
import android.widget.Toast;
import android.widget.ListView;
import android.widget.ImageView;
import android.widget.TextView;
import androidx.appcompat.widget.Toolbar;
import android.os.Bundle;
import android.util.Patterns;
import android.Manifest;
import android.os.Build;
import androidx.core.app.ActivityCompat;
import android.content.Context;
import android.hardware.camera2.CameraManager;

import java.util.ArrayList;
import java.util.regex.Pattern;
import java.util.regex.Matcher;
import java.util.Map;


public class ServerListActivity extends AppCompatActivity  {
    ListView listView;
    EditText serverName;
    EditText serverAddress;
    ImageView add;
    ArrayList<Server> ItemModelList;
    ServerListAdapter serverListAdapter;
    SeekBar seekBar = null;
    TextView intervalLabel = null;
    Switch subtitles = null;
    Switch serverStateful = null;
    EditText speechDedupInterval = null;
    CameraManager camMan = null;
    private SharedPreferences mSharedPreferences;
    private static final int MY_PERMISSIONS_REQUEST_CAMERA = 23;


    //activity menu
    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        // Inflate the menu; this adds items to the action bar if it is present.
        getMenuInflater().inflate(R.menu.main, menu);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        // Handle action bar item clicks here. The action bar will
        // automatically handle clicks on the Home/Up button, so long
        // as you specify a parent activity in AndroidManifest.xml.
        int id = item.getItemId();
        switch (id) {
            case R.id.about:
                AlertDialog.Builder builder = new AlertDialog.Builder(this);
                builder.setMessage(R.string.about_message)
                        .setTitle(R.string.about_title);
                AlertDialog dialog = builder.create();
                dialog.show();
                return true;
            default:
                return false;
        }
    }


    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestPermission();
        
        setContentView(R.layout.activity_serverlist);
        Toolbar myToolbar = (Toolbar) findViewById(R.id.toolbar);
        setSupportActionBar(myToolbar);
        listView = (ListView) findViewById(R.id.listServers);
        serverName = (EditText) findViewById(R.id.addServerName);
        serverAddress = (EditText) findViewById(R.id.addServerAddress);
        add = (ImageView) findViewById(R.id.imgViewAdd);
        ItemModelList = new ArrayList<Server>();
        serverListAdapter = new ServerListAdapter(this, ItemModelList);
        listView.setAdapter(serverListAdapter);
        mSharedPreferences=getSharedPreferences(getString(R.string.shared_preference_file_key),
                MODE_PRIVATE);

        // app options
        // subtitle
        subtitles = (Switch) findViewById(R.id.subtitles);
        subtitles.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                Const.SHOW_SUBTITLES = isChecked;
                SharedPreferences.Editor editor = mSharedPreferences.edit();
                editor.putBoolean("option:subtitles",isChecked);
                editor.commit();
            }
        });
        subtitles.setChecked(mSharedPreferences.getBoolean("option:subtitles", false));

        // stateful server
        // if server is stateful, DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT should be
        // turned off.
        serverStateful = (Switch) findViewById(R.id.serverStateful);
        serverStateful.setOnCheckedChangeListener(new CompoundButton.OnCheckedChangeListener() {
            public void onCheckedChanged(CompoundButton buttonView, boolean isChecked) {
                Const.DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT = !isChecked;
                SharedPreferences.Editor editor = mSharedPreferences.edit();
                editor.putBoolean("option:serverStateful",
                        isChecked);
                editor.commit();
            }
        });
        boolean initialServerStateful= mSharedPreferences.getBoolean("option:serverStateful",
                !Const.DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT);
        Const.DEDUPLICATE_RESPONSE_BY_ENGINE_UPDATE_COUNT = !initialServerStateful;
        serverStateful.setChecked(initialServerStateful);

        // dedup interval
        speechDedupInterval = (EditText) findViewById(R.id.speechDedupInterval);
        speechDedupInterval.addTextChangedListener(
                new TextWatcher() {
                    @Override
                    public void beforeTextChanged(CharSequence s, int start, int count, int after) {
                    }
                    @Override
                    public void onTextChanged(CharSequence s, int start, int before, int count) {
                    }
                    @Override
                    public void afterTextChanged(Editable s) {
                        try {
                            Const.SPEECH_DEDUP_INTERVAL = Integer.parseInt(s.toString());
                            SharedPreferences.Editor editor = mSharedPreferences.edit();
                            editor.putInt("option:speechDedupInterval",
                                    Const.SPEECH_DEDUP_INTERVAL);
                            editor.commit();
                        } catch (NumberFormatException e) {
                            Log.e(this.getClass().getName(), "Invalid int string (" +
                                    s.toString() + ").. Failed to set SPEECH_DEDUP_INTERVAL");
                        }
                    }
                }
        );
        Const.SPEECH_DEDUP_INTERVAL = mSharedPreferences.getInt("option:speechDedupInterval",
                Const.SPEECH_DEDUP_INTERVAL);
        speechDedupInterval.setText(String.valueOf(Const.SPEECH_DEDUP_INTERVAL));

        camMan = (CameraManager) getSystemService(Context.CAMERA_SERVICE);

        initServerList();
    }

    private void requestPermission() {
        String permissions[] = {Manifest.permission.CAMERA,
                Manifest.permission.WRITE_EXTERNAL_STORAGE
        };
        if (android.os.Build.VERSION.SDK_INT >= Build.VERSION_CODES.M){
            ActivityCompat.requestPermissions(this,
                    permissions,
                    MY_PERMISSIONS_REQUEST_CAMERA);
        }
    }


    private void initServerList(){
        Map<String, ?> prefs = mSharedPreferences.getAll();
        for (Map.Entry<String,?> pref : prefs.entrySet())
            if(pref.getKey().startsWith("server:")) {
                Server s = new Server(pref.getKey().substring("server:".length()), pref.getValue().toString());
                ItemModelList.add(s);
                serverListAdapter.notifyDataSetChanged();
            }
    }

    public void addValue(View v) {
        add.performHapticFeedback(android.view.HapticFeedbackConstants.LONG_PRESS);
        String name = serverName.getText().toString();
        String endpoint = serverAddress.getText().toString();
        if (name.isEmpty() || endpoint.isEmpty()) {
            Toast.makeText(getApplicationContext(), R.string.error_empty ,
                    Toast.LENGTH_SHORT).show();
        } else if(ValidateEndpoint(endpoint, Const.PORT) == null) {
            Toast.makeText(getApplicationContext(), R.string.error_invalidURI,
                    Toast.LENGTH_SHORT).show();
        }  else if(mSharedPreferences.contains("server:".concat(name))) {
            Toast.makeText(getApplicationContext(), R.string.error_exists,
                Toast.LENGTH_SHORT).show();
        } else {
            Server s = new Server(name, endpoint);
            ItemModelList.add(s);
            serverListAdapter.notifyDataSetChanged();
            serverName.setText("");
            serverAddress.setText("");
            SharedPreferences.Editor editor = mSharedPreferences.edit();
            editor.putString("server:".concat(name),endpoint);
            editor.commit();
            findViewById(R.id.textOptions).requestFocus();
        }
    }




}
