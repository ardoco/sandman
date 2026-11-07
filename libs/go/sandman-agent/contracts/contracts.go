// Package contracts validates instances against contracts/*.schema.json without network access.
package contracts

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

var schemaFiles = []string{"common.schema.json", "skills.schema.json", "events.schema.json", "knowledge.schema.json"}

// Error reports a failed validation. Ref is the schema reference as given by the caller.
type Error struct {
	Ref      string
	Messages []string
}

func (e *Error) Error() string { return e.Ref + ": " + strings.Join(e.Messages, "; ") }

type Contracts struct {
	compiler *jsonschema.Compiler
	base     map[string]string
	cache    map[string]*jsonschema.Schema
}

// New loads the schemas of dir. Format keywords are not enforced, as in the Python kit.
func New(dir string) (*Contracts, error) {
	c := &Contracts{compiler: jsonschema.NewCompiler(), base: map[string]string{}, cache: map[string]*jsonschema.Schema{}}
	extra, err := filepath.Glob(filepath.Join(dir, "config", "*.schema.json"))
	if err != nil {
		return nil, err
	}
	sort.Strings(extra)
	names := append([]string(nil), schemaFiles...)
	for _, f := range extra {
		names = append(names, "config/"+filepath.Base(f))
	}
	for _, name := range names {
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			return nil, err
		}
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
		if err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		var head struct {
			ID string `json:"$id"`
		}
		if err := json.Unmarshal(raw, &head); err != nil || head.ID == "" {
			return nil, fmt.Errorf("%s: missing $id", name)
		}
		c.base[name] = head.ID
		if err := c.compiler.AddResource(head.ID, doc); err != nil {
			return nil, err
		}
	}
	return c, nil
}

func (c *Contracts) uri(ref string) string {
	file, frag, hasFrag := strings.Cut(ref, "#")
	if b, ok := c.base[file]; ok {
		file = b
	}
	if hasFrag {
		return file + "#" + frag
	}
	return file
}

// Validate checks instance, which must come from encoding/json (use json.Number or float64 for numbers).
func (c *Contracts) Validate(ref string, instance any) error {
	uri := c.uri(ref)
	schema, ok := c.cache[uri]
	if !ok {
		var err error
		if schema, err = c.compiler.Compile(uri); err != nil {
			return fmt.Errorf("compile %s: %w", ref, err)
		}
		c.cache[uri] = schema
	}
	err := schema.Validate(normalize(instance))
	if err == nil {
		return nil
	}
	var ve *jsonschema.ValidationError
	if errors.As(err, &ve) {
		var msgs []string
		for _, leaf := range leaves(ve) {
			loc := "/" + strings.Join(leaf.InstanceLocation, "/")
			if len(leaf.InstanceLocation) == 0 {
				loc = "<root>"
			}
			msgs = append(msgs, loc+": "+leaf.ErrorKind.LocalizedString(messagePrinter))
		}
		return &Error{Ref: ref, Messages: msgs}
	}
	return err
}

func (c *Contracts) IsValid(ref string, instance any) bool {
	var ce *Error
	err := c.Validate(ref, instance)
	if err != nil && !errors.As(err, &ce) {
		panic(err)
	}
	return err == nil
}

func leaves(e *jsonschema.ValidationError) []*jsonschema.ValidationError {
	if len(e.Causes) == 0 {
		return []*jsonschema.ValidationError{e}
	}
	var out []*jsonschema.ValidationError
	for _, cause := range e.Causes {
		out = append(out, leaves(cause)...)
	}
	return out
}

// normalize converts typed Go values (structs, ints) to the generic JSON form the validator expects.
func normalize(v any) any {
	raw, err := json.Marshal(v)
	if err != nil {
		return v
	}
	out, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return v
	}
	return out
}
