// Copyright 2020 Carnegie Mellon University
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

package edu.cmu.cs.gabriel.serverlist;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.preference.PreferenceManager;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.BaseAdapter;
import android.widget.ImageView;
import android.widget.TextView;
import android.widget.Toast;


import androidx.recyclerview.widget.RecyclerView;

import java.util.ArrayList;

class ServerListAdapter extends RecyclerView.Adapter<ServerListAdapter.ViewHolder>  {

    // Member variables.
    private ArrayList<Server> mData;
    private Context mContext;
    private SharedPreferences mSharedPreferences;
    private String mClass;
    private String mPackage;


    /**
     * Constructor that passes in the sports data and the context.
     *
     * @param serverData ArrayList containing the sports data.
     * @param context Context of the application.
     */
    ServerListAdapter(Context context, ArrayList<Server> serverData, String c, String p) {
        this.mData = serverData;
        this.mContext = context;
        this.mSharedPreferences= PreferenceManager.getDefaultSharedPreferences(context);
        this.mClass = c;
        this.mPackage = p;
    }


    /**
     * Required method for creating the viewholder objects.
     *
     * @param parent The ViewGroup into which the new View will be added
     *               after it is bound to an adapter position.
     * @param viewType The view type of the new View.
     * @return The newly created ViewHolder.
     */
    @Override
    public ServerListAdapter.ViewHolder onCreateViewHolder(
            ViewGroup parent, int viewType) {
        return new ViewHolder(LayoutInflater.from(mContext).
                inflate(R.layout.list_item, parent, false));
    }

    /**
     * Required method that binds the data to the viewholder.
     *
     * @param holder The viewholder into which the data should be put.
     * @param position The adapter position.
     */
    @Override
    public void onBindViewHolder(ServerListAdapter.ViewHolder holder,
                                 int position) {
        // Get current sport.
        Server current = mData.get(position);

        // Populate the textviews with data.
        holder.bindTo(current);
    }

    /**
     * Required method for determining the size of the data set.
     *
     * @return Size of the data set.
     */
    @Override
    public int getItemCount() {
        return mData.size();
    }


    /**
     * ViewHolder class that represents each row of data in the RecyclerView.
     */
    class ViewHolder extends RecyclerView.ViewHolder
            implements View.OnClickListener{

        // Member Variables for the TextViews
        private TextView mTitleText;
        private TextView mInfoText;

        /**
         * Constructor for the ViewHolder, used in onCreateViewHolder().
         *
         * @param itemView The rootview of the list_item.xml layout file.
         */
        ViewHolder(View itemView) {
            super(itemView);

            // Initialize the views.
            mTitleText = itemView.findViewById(R.id.serverName);
            mInfoText = itemView.findViewById(R.id.serverAddress);

            // Set the OnClickListener to the entire view.
            itemView.setOnClickListener(this);
        }

        void bindTo(Server current){
            // Populate the textviews with data.
            mTitleText.setText(current.getName());
            mInfoText.setText(current.getEndpoint());

        }

        /**
         * Handle click to show DetailActivity.
         *
         * @param view View that is clicked.
         */
        @Override
        public void onClick(View view) {
            Server current = mData.get(getAdapterPosition());
            Intent detailIntent = new Intent();
            detailIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            detailIntent.setClassName(mPackage,mClass);
            detailIntent.putExtra("SERVER_IP", current.getEndpoint());
            mContext.startActivity(detailIntent);


        }
    }
}