package edu.cmu.cs.gabriel.client.function;

// We have to create this because java.util.function.Supplier requires Android API 24 and Google
// Glass will only support up to API 19.
public interface Supplier<T> {
    T get();
}
