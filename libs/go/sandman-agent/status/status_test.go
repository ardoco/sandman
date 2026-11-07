package status

import "testing"

func TestWorkingWhileTaskRuns(t *testing.T) {
	tr := NewTracker("svc")
	if tr.Snapshot(nil)["state"] != "idle" {
		t.Fatal("not idle")
	}
	var during map[string]any
	_ = tr.Track(func() error { during = tr.Snapshot(nil); return nil })
	if during["state"] != "working" || during["activeTasks"] != 1 {
		t.Fatal(during)
	}
	after := tr.Snapshot(nil)
	if after["state"] != "idle" || after["activeTasks"] != 0 || after["lastActivityAt"] == nil {
		t.Fatal(after)
	}
}
