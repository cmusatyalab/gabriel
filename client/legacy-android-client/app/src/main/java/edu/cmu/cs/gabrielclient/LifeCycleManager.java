package edu.cmu.cs.gabrielclient;

import java.util.ArrayList;

import edu.cmu.cs.gabrielclient.util.LifeCycleIF;


/**
 * Manages sensor streams
 */
public class LifeCycleManager implements LifeCycleIF {
    // singleton class
    private static LifeCycleManager instance = null;
    private ArrayList<LifeCycleIF> items = new ArrayList<>();

    private LifeCycleManager() {
    }

    public static LifeCycleManager getInstance() {
        if (instance == null) {
            instance = new LifeCycleManager();
        }
        return instance;
    }

    public void add(LifeCycleIF s) {
        items.add(s);
    }

    @Override
    public void onResume() {
        for (LifeCycleIF s : items) {
            s.onResume();
        }
    }

    @Override
    public void onPause() {
        for (LifeCycleIF s : items) {
            s.onPause();
        }
    }

    @Override
    public void onDestroy() {
        for (LifeCycleIF s : items) {
            s.onDestroy();
        }
    }
}
