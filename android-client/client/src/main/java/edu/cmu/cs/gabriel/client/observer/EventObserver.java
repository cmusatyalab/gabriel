package edu.cmu.cs.gabriel.client.observer;

import android.util.Log;

import com.tinder.scarlet.Lifecycle;
import com.tinder.scarlet.ShutdownReason;
import com.tinder.scarlet.Stream.Observer;
import com.tinder.scarlet.WebSocket.Event;
import com.tinder.scarlet.lifecycle.LifecycleRegistry;

import org.jetbrains.annotations.NotNull;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.results.ErrorType;

public class EventObserver implements Observer<Event> {
    private static final String TAG = "EventObserver";

    private final LifecycleRegistry lifecycleRegistry;
    private final Consumer<ErrorType> onDisconnect;
    private boolean running;

    public EventObserver(Consumer<ErrorType> onDisconnect) {
        this.lifecycleRegistry = new LifecycleRegistry(0L);
        this.lifecycleRegistry.onNext(Lifecycle.State.Started.INSTANCE);
        this.onDisconnect = onDisconnect;
        this.running = false;
    }

    @Override
    public void onNext(Event receivedUpdate) {
        if (!(receivedUpdate instanceof Event.OnMessageReceived)) {
            Log.i(TAG, receivedUpdate.toString());

            if (receivedUpdate instanceof Event.OnConnectionOpened) {
                this.running = true;
            } else if (receivedUpdate instanceof Event.OnConnectionFailed) {
                ErrorType errorType = this.running
                        ? ErrorType.SERVER_DISCONNECTED
                        : ErrorType.COULD_NOT_CONNECT;
                this.onDisconnect.accept(errorType);
                this.stop();  // Will prevent Scarlet from trying to reconnect
            }

            // We do not check for Event.OnConnectionClosed because this is what gets sent when the
            // User presses the home button. Scarlet will automatically reconnect when a user
            // returns to the app.
        }
    }

    public LifecycleRegistry getLifecycleRegistry() {
        return lifecycleRegistry;
    }

    public boolean isRunning() {
        return this.running;
    }

    public void stop() {
        this.running = false;
        this.lifecycleRegistry.onNext(
                new Lifecycle.State.Stopped.WithReason(ShutdownReason.GRACEFUL));
    }

    @Override
    public void onError(@NotNull Throwable throwable) {
        Log.e(TAG, "Event onError", throwable);

    }

    @Override
    public void onComplete() {
        Log.i(TAG, "Event onComplete");
    }
}
