package contracts

import (
	"errors"
	"testing"
)

const dir = "../../../../contracts"

func TestValidateSkillInput(t *testing.T) {
	c, err := New(dir)
	if err != nil {
		t.Fatal(err)
	}
	const ref = "https://sandman.invalid/contracts/skills.schema.json#/$defs/RecoverTraceLinksInput"
	good := map[string]any{
		"projectId":        "mediastore",
		"sourceVersionIds": []any{"ver_0123456789abcdef0123456789abcdef"},
		"targetVersionIds": []any{"ver_fedcba9876543210fedcba9876543210"},
	}
	if err := c.Validate(ref, good); err != nil {
		t.Fatal(err)
	}
	var ce *Error
	if err := c.Validate(ref, map[string]any{"projectId": "Mediastore!"}); !errors.As(err, &ce) || len(ce.Messages) == 0 {
		t.Fatalf("want contract error, got %v", err)
	}
	if c.IsValid("events.schema.json", map[string]any{}) {
		t.Fatal("empty event valid")
	}
}
