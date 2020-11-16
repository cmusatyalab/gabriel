package edu.cmu.cs.gabriel.serverlist;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.DialogInterface;
import android.content.SharedPreferences;
import android.hardware.camera2.CameraManager;
import android.os.Bundle;
import android.preference.PreferenceManager;
import android.util.Log;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.Toast;

import androidx.appcompat.widget.Toolbar;
import androidx.fragment.app.Fragment;
import androidx.recyclerview.widget.ItemTouchHelper;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;

import com.google.android.material.floatingactionbutton.FloatingActionButton;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Map;
import java.util.UUID;

import edu.cmu.cs.gabriel.client.socket.SocketWrapper;


public class ServerListFragment  extends Fragment {

    // Member variables.
    private RecyclerView mRecyclerView;
    private ArrayList<Server> mData;
    private ServerListAdapter mAdapter;
    private SharedPreferences mSharedPreferences;
    private String mPackage;
    private String mClass;

    public ServerListFragment(String p, String c) {
        mPackage = p;
        mClass = c;
    }

    public ServerListFragment() {

    }
    @Override
    public View onCreateView(LayoutInflater inflater, ViewGroup container,
                             Bundle savedInstanceState) {

        View v = inflater.inflate(R.layout.fragment_serverlist, container, false);
        FloatingActionButton fab = v.findViewById(R.id.fab);
        fab.setOnClickListener(new View.OnClickListener() {
            public void onClick(View v) {
                AlertDialog.Builder builder = new AlertDialog.Builder(v.getContext());
                // Get the layout inflater

                View inf = inflater.inflate(R.layout.dialog_add_server, null);
                final EditText name = inf.findViewById(R.id.addServerName);
                final EditText endpoint = inf.findViewById(R.id.addServerAddress);
                // Inflate and set the layout for the dialog
                // Pass null as the parent view because its going in the dialog layout
                builder.setView(inf)
                        .setTitle(R.string.add_server)
                        // Add action buttons
                        .setPositiveButton(R.string.add, (dialog, id) -> {
                            String n = name.getText().toString();
                            String e = endpoint.getText().toString();
                            if (n.isEmpty() || e.isEmpty()) {
                                Toast.makeText(v.getContext(), R.string.error_empty ,
                                        Toast.LENGTH_SHORT).show();
                            } else if (!SocketWrapper.validUri(e, 9099)) {
                                Toast.makeText(v.getContext(), R.string.error_invalidURI,
                                        Toast.LENGTH_SHORT).show();
                            }  else if(mSharedPreferences.contains("server:".concat(n))) {
                                Toast.makeText(v.getContext(), R.string.error_exists,
                                        Toast.LENGTH_SHORT).show();
                            } else {
                                Server s = new Server(n, e);
                                mData.add(s);
                                mAdapter.notifyDataSetChanged();
                                SharedPreferences.Editor editor = mSharedPreferences.edit();
                                editor.putString("server:".concat(n),e);
                                editor.commit();
                            }
                        })
                        .setNegativeButton(R.string.cancel, new DialogInterface.OnClickListener() {
                            public void onClick(DialogInterface dialog, int id) {

                            }
                        });
                AlertDialog addDialog = builder.create();
                addDialog.show();
            }
        });
        // Initialize the RecyclerView.
        mRecyclerView = v.findViewById(R.id.recyclerView);

        // Set the Layout Manager.
        mRecyclerView.setLayoutManager(new LinearLayoutManager(v.getContext()));

        // Initialize the ArrayList that will contain the data.
        mData = new ArrayList<>();

        // Initialize the adapter and set it to the RecyclerView.
        mAdapter = new ServerListAdapter(v.getContext(), mData, mClass, mPackage);
        mRecyclerView.setAdapter(mAdapter);
        mSharedPreferences= PreferenceManager.getDefaultSharedPreferences(v.getContext());

        // Get the data.
        initServerList();

        // Helper class for creating swipe to dismiss and drag and drop
        // functionality.
        ItemTouchHelper helper = new ItemTouchHelper(new ItemTouchHelper
                .SimpleCallback(
                ItemTouchHelper.DOWN | ItemTouchHelper.UP,
                ItemTouchHelper.LEFT | ItemTouchHelper.RIGHT) {
            /**
             * Defines the drag and drop functionality.
             *
             * @param recyclerView The RecyclerView that contains the list items
             * @param viewHolder The SportsViewHolder that is being moved
             * @param target The SportsViewHolder that you are switching the
             *               original one with.
             * @return true if the item was moved, false otherwise
             */
            @Override
            public boolean onMove(RecyclerView recyclerView,
                                  RecyclerView.ViewHolder viewHolder,
                                  RecyclerView.ViewHolder target) {
                // Get the from and to positions.
                int from = viewHolder.getAdapterPosition();
                int to = target.getAdapterPosition();

                // Swap the items and notify the adapter.
                Collections.swap(mData, from, to);
                mAdapter.notifyItemMoved(from, to);
                return true;
            }

            /**
             * Defines the swipe to dismiss functionality.
             *
             * @param viewHolder The viewholder being swiped.
             * @param direction The direction it is swiped in.
             */
            @Override
            public void onSwiped(RecyclerView.ViewHolder viewHolder,
                                 int direction) {
                Server s = mData.get(viewHolder.getAdapterPosition());
                // Remove the item from the dataset.
                mData.remove(viewHolder.getAdapterPosition());
                // Notify the adapter.
                mAdapter.notifyItemRemoved(viewHolder.getAdapterPosition());
                SharedPreferences.Editor editor = mSharedPreferences.edit();
                editor.remove("server:".concat(s.name));
                editor.commit();
            }
        });

        // Attach the helper to the RecyclerView.
        helper.attachToRecyclerView(mRecyclerView);

        Map<String, ?> m = mSharedPreferences.getAll();
        for(Map.Entry<String,?> entry : m.entrySet()){
            Log.d("SharedPreferences",entry.getKey() + ": " +
                    entry.getValue().toString());

        }

        // Inflate the layout for this fragment
        return v;
    }

    void initServerList() {
        Map<String, ?> prefs = mSharedPreferences.getAll();
        boolean uuid_set = false;
        for (Map.Entry<String,?> pref : prefs.entrySet())
            if(pref.getKey().startsWith("server:")) {
                Server s = new Server(pref.getKey().substring("server:".length()), pref.getValue().toString());
                mData.add(s);
                mAdapter.notifyDataSetChanged();
            }

        if (prefs.isEmpty()) {
            // Add demo server if there are no other servers present
            Server s = new Server(getString(R.string.demo_server), getString(R.string.demo_dns));
            mData.add(s);
            mAdapter.notifyDataSetChanged();
            SharedPreferences.Editor editor = mSharedPreferences.edit();
            editor.putString("server:".concat(getString(R.string.demo_server)),getString(R.string.demo_dns));
            editor.commit();
        }
    }



}
