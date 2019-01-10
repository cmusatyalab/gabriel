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

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;


import java.util.ArrayList;

public class ServerListAdapter extends BaseAdapter {
    Context context;
    ArrayList<Server> itemModelList;
    SharedPreferences mSharedPreferences = null;

    public ServerListAdapter(Context context, ArrayList<Server> modelList) {
        this.context = context;
        this.itemModelList = modelList;
        mSharedPreferences=context.getSharedPreferences(context.getString(R.string.shared_preference_file_key),
                Context.MODE_PRIVATE);
    }
    @Override
    public int getCount() {
        return itemModelList.size();
    }
    @Override
    public Object getItem(int position) {
        return itemModelList.get(position);
    }
    @Override
    public long getItemId(int position) {
        return position;
    }
    @Override
    public View getView(final int position, View convertView, ViewGroup parent) {
        convertView = null;
        if (convertView == null) {
            LayoutInflater mInflater = (LayoutInflater) context
                    .getSystemService(Activity.LAYOUT_INFLATER_SERVICE);
            convertView = mInflater.inflate(R.layout.list_item, null);
            TextView serverName = convertView.findViewById(R.id.serverName);
            TextView serverAddress = convertView.findViewById(R.id.serverAddress);
            ImageView imgRemove = convertView.findViewById(R.id.imgRemove);
            ImageView imgConnect = convertView.findViewById(R.id.imgConnect);
            Server s = itemModelList.get(position);
            serverName.setText(s.getName());
            serverAddress.setText(s.getEndpoint());
            imgConnect.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    Server s = itemModelList.get(position);
                    Const.SERVER_IP = s.getEndpoint();
                    Intent intent = new Intent(context, GabrielClientActivity.class);
                    intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    //intent.putExtra("", faceTable);
                    context.startActivity(intent);

                    Toast.makeText(context, R.string.connecting_toast, Toast.LENGTH_SHORT).show();
                }
            });
            imgRemove.setOnClickListener(new View.OnClickListener() {
                @Override
                public void onClick(View v) {
                    SharedPreferences.Editor editor = mSharedPreferences.edit();
                    Server s = itemModelList.get(position);

                    editor.remove("server:".concat(s.getName()));
                    editor.commit();
                    itemModelList.remove(position);
                    notifyDataSetChanged();
                    Toast.makeText(context, String.format("%s %s", context.getString(R.string.server_removed), s.getName()),
                            Toast.LENGTH_SHORT).show();
                }
            });
        }
        return convertView;
    }
}