package edu.cmu.cs.gabriel.client.observer;

import android.util.Log;

import com.tinder.scarlet.Lifecycle;
import com.tinder.scarlet.ShutdownReason;
import com.tinder.scarlet.Stream.Observer;
import com.tinder.scarlet.WebSocket.Event;
import com.tinder.scarlet.lifecycle.LifecycleRegistry;

import edu.cmu.cs.gabriel.client.token.TokenManager;

public class EventObserver implements Observer<Event> {
    private static final String TAG = "EventObserver";

    private TokenManager tokenManager;
    private Runnable onConnectionProblem;
    private LifecycleRegistry lifecycleRegistry;

    public EventObserver(TokenManager tokenManager, Runnable onConnectionProblem,
                         LifecycleRegistry lifecycleRegistry) {
        this.tokenManager = tokenManager;
        this.onConnectionProblem = onConnectionProblem;
        this.lifecycleRegistry = lifecycleRegistry;
    }

    @Override
    public void onNext(Event receivedUpdate) {
        if (!(receivedUpdate instanceof Event.OnMessageReceived)) {
            Log.i(TAG, receivedUpdate.toString());

            if (receivedUpdate instanceof Event.OnConnectionOpened) {
                this.tokenManager.start();
            } else if (receivedUpdate instanceof Event.OnConnectionFailed) {
                this.onConnectionProblem.run();

                // Do not try to reconnect
                this.lifecycleRegistry.onNext(
                        new Lifecycle.State.Stopped.WithReason(ShutdownReason.GRACEFUL));
            }

            // We do not check for Event.OnConnectionClosed because this is what gets sent when the
            // User presses the home button. Scarlet will automatically reconnect when a user
            // returns to the app.
        }
    }

    @Override
    public void onError(Throwable throwable) {
        Log.e(TAG, "Event onError", throwable);

    }

    @Override
    public void onComplete() {
        Log.i(TAG, "Event onComplete");
    }
}
