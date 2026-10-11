package com.acme;

import com.acme.sub.Helper;
import java.util.ArrayList;
import java.util.List;

/** Outer class with a nested class, a nested enum and overloaded methods. */
public class Outer implements Shape {
    private final List<String> names = new ArrayList<>();

    public void run() {
        names.add(Helper.help());
    }

    public void run(int times) {
        for (int i = 0; i < times; i++) {
            run();
        }
    }

    public void run(String name) {
        names.add(name);
    }

    @Override
    public double area() {
        return names.size();
    }

    @Override
    public String describe() {
        return String.join(",", names);
    }

    static class Inner {
        void go() {
            System.out.println("go");
        }
    }

    enum Color {
        RED,
        GREEN;

        String paint() {
            return name().toLowerCase();
        }
    }
}
