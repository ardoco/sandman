package ids

import (
	"encoding/json"
	"os"
	"regexp"
	"strconv"
	"testing"
)

type vecFile struct {
	StableID struct {
		Cases []struct {
			Prefix   string
			Parts    []string
			Expected string
		}
	}
	SectionKey struct {
		Cases []struct {
			Headings    []string
			Occurrences []int
			Expected    string
		}
	}
	SentenceKey struct {
		Cases []struct {
			SectionLocalKey string
			Text            string
			Occurrence      int
			Expected        string
		}
	}
	ConceptName struct {
		Cases []struct{ Name, Expected string }
	}
	Fingerprint struct {
		Cases []struct {
			Input struct {
				Type, Detector string
				Involved       []string
			}
			Expected, ProjectID, ExpectedInconsistencyID string
		}
	}
	EvidenceHash struct {
		Cases []struct {
			Name     string
			Involved struct {
				Elements []struct{ Key, ContentHash string }
				Links    []struct {
					Endpoints []struct{ Key, ContentHash string }
				}
			}
			SortedPairs []string
			Expected    string
		}
	}
	IngestionID struct {
		Cases []struct {
			Input struct {
				ProjectID string
				Seq       int
			}
			Expected string
		}
	}
	TraceLinkID struct {
		Cases []struct {
			Input    struct{ Approach, ApproachVersion, ConfigHash, SourceID, TargetID string }
			Expected string
		}
	}
	InputKey    canonCases
	ActionKey   canonCases
	CassetteKey struct {
		Cases []struct {
			Request     json.RawMessage
			Canonical   string
			ExpectedKey string
		}
	}
}

type canonCases struct {
	Cases []struct {
		Name      string
		Object    json.RawMessage
		Canonical string
		Expected  string
	}
}

func load(t *testing.T) vecFile {
	raw, err := os.ReadFile("../../../../contracts/conformance/vectors.json")
	if err != nil {
		t.Fatal(err)
	}
	var v vecFile
	if err := json.Unmarshal(raw, &v); err != nil {
		t.Fatal(err)
	}
	return v
}

func TestConformance(t *testing.T) {
	v := load(t)
	for _, c := range v.StableID.Cases {
		if got := StableID(c.Prefix, c.Parts...); got != c.Expected {
			t.Errorf("stableId %s: got %s", c.Expected, got)
		}
	}
	for _, c := range v.SectionKey.Cases {
		if got, err := SectionLocalKey(c.Headings, c.Occurrences); err != nil || got != c.Expected {
			t.Errorf("sectionKey %s: got %s %v", c.Expected, got, err)
		}
	}
	for _, c := range v.SentenceKey.Cases {
		if got := SentenceLocalKey(c.SectionLocalKey, c.Text, c.Occurrence); got != c.Expected {
			t.Errorf("sentenceKey %s: got %s", c.Expected, got)
		}
	}
	for _, c := range v.ConceptName.Cases {
		if got := NormalizeConceptName(c.Name); got != c.Expected {
			t.Errorf("conceptName %q: got %q want %q", c.Name, got, c.Expected)
		}
	}
	for _, c := range v.Fingerprint.Cases {
		fp := Fingerprint(c.Input.Type, c.Input.Detector, c.Input.Involved)
		if fp != c.Expected {
			t.Errorf("fingerprint %s: got %s", c.Expected, fp)
		}
		if c.ExpectedInconsistencyID != "" && StableID("inc", c.ProjectID, fp) != c.ExpectedInconsistencyID {
			t.Errorf("inconsistency id %s", c.ExpectedInconsistencyID)
		}
	}
	for _, c := range v.EvidenceHash.Cases {
		var pairs []string
		for _, e := range c.Involved.Elements {
			pairs = append(pairs, e.Key+"="+e.ContentHash)
		}
		for _, l := range c.Involved.Links {
			for _, e := range l.Endpoints {
				pairs = append(pairs, e.Key+"="+e.ContentHash)
			}
		}
		if got := EvidenceHash(pairs); got != c.Expected {
			t.Errorf("evidenceHash %s: got %s", c.Name, got)
		}
	}
	for _, c := range v.IngestionID.Cases {
		if got := StableID("ing", c.Input.ProjectID, strconv.Itoa(c.Input.Seq)); got != c.Expected {
			t.Errorf("ingestionId %s: got %s", c.Expected, got)
		}
	}
	for _, c := range v.TraceLinkID.Cases {
		i := c.Input
		if got := StableID("tl", i.Approach, i.ApproachVersion, i.ConfigHash, i.SourceID, i.TargetID); got != c.Expected {
			t.Errorf("traceLinkId %s: got %s", c.Expected, got)
		}
	}
	for _, group := range []canonCases{v.InputKey, v.ActionKey} {
		for _, c := range group.Cases {
			got, err := CanonicalJSONBytes(c.Object)
			if err != nil || got != c.Canonical {
				t.Errorf("canonical %s: got %s %v", c.Name, got, err)
			}
			if Sha256Hex(c.Canonical) != c.Expected {
				t.Errorf("key %s", c.Name)
			}
		}
	}
	for _, c := range v.CassetteKey.Cases {
		var body map[string]json.RawMessage
		if err := json.Unmarshal(c.Request, &body); err != nil {
			t.Fatal(err)
		}
		delete(body, "user")
		delete(body, "stream")
		delete(body, "stream_options")
		raw, _ := json.Marshal(body)
		got, err := CanonicalJSONBytes(raw)
		if err != nil || got != c.Canonical {
			t.Errorf("cassette canonical: got %s %v", got, err)
		}
		if Sha256Hex(c.Canonical) != c.ExpectedKey {
			t.Errorf("cassette key %s", c.ExpectedKey)
		}
	}
}

func TestGeneratedIDs(t *testing.T) {
	if !regexp.MustCompile(`^run_[0-9a-f]{32}$`).MatchString(NewRunID()) || !regexp.MustCompile(`^wf_[0-9a-f]{32}$`).MatchString(NewWorkflowID()) {
		t.Fatal("bad id format")
	}
}
