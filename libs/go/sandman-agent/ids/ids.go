// Package ids implements the language-neutral key and hash rules of contracts/conformance/vectors.json.
package ids

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/gowebpki/jcs"
	"golang.org/x/text/cases"
	"golang.org/x/text/unicode/norm"
)

const Sep = "\x1f"

var (
	wsRun    = regexp.MustCompile(`[ \t\n\r\f\v]+`)
	nonSlug  = regexp.MustCompile(`[^a-z0-9]+`)
	foldCase = cases.Fold()
)

func Sha256Hex(data string) string {
	sum := sha256.Sum256([]byte(data))
	return hex.EncodeToString(sum[:])
}

func StableID(prefix string, parts ...string) string {
	return prefix + "_" + Sha256Hex(strings.Join(parts, Sep))[:32]
}

// CanonicalJSON serializes value per RFC 8785. Numbers keep their json.Number text only if the value is a json.Number.
func CanonicalJSON(value any) (string, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return "", err
	}
	out, err := jcs.Transform(raw)
	return string(out), err
}

// CanonicalJSONBytes canonicalizes a JSON document, decoding numbers as json.Number.
func CanonicalJSONBytes(doc []byte) (string, error) {
	var v any
	dec := json.NewDecoder(bytes.NewReader(doc))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return "", err
	}
	return CanonicalJSON(v)
}

func asciiLower(s string) string {
	return strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' {
			return r + 32
		}
		return r
	}, s)
}

func slug(heading string) string {
	s := strings.Trim(nonSlug.ReplaceAllString(asciiLower(norm.NFC.String(heading)), "-"), "-")
	if s == "" {
		return "section"
	}
	return s
}

func SectionLocalKey(headings []string, occurrences []int) (string, error) {
	if len(headings) != len(occurrences) {
		return "", fmt.Errorf("headings and occurrences differ in length")
	}
	parts := make([]string, len(headings))
	for i, h := range headings {
		parts[i] = slug(h)
		if occurrences[i] >= 2 {
			parts[i] += "~" + strconv.Itoa(occurrences[i])
		}
	}
	return "sec:" + strings.Join(parts, "/"), nil
}

func trimWS(s string) string { return strings.Trim(s, " \t\n\r\f\v") }

func SentenceLocalKey(sectionKey, text string, occurrence int) string {
	normalized := trimWS(wsRun.ReplaceAllString(norm.NFC.String(text), " "))
	key := sectionKey + "/" + Sha256Hex(normalized)[:12]
	if occurrence >= 2 {
		key += "~" + strconv.Itoa(occurrence)
	}
	return key
}

// NormalizeConceptName uses x/text full case folding (ss for U+00DF). Divergence from the TS kit: none known for the
// vectors; the TS kit approximates folding with toUpperCase().toLowerCase().
func NormalizeConceptName(name string) string {
	folded := norm.NFC.String(foldCase.String(norm.NFC.String(name)))
	return trimWS(wsRun.ReplaceAllString(folded, " "))
}

func sortedBytes(in []string) []string {
	out := append([]string(nil), in...)
	sort.Strings(out) // Go string order is byte-wise
	return out
}

func Fingerprint(typ, detectorID string, involvedKeys []string) string {
	return Sha256Hex(strings.Join(append([]string{typ, detectorID}, sortedBytes(involvedKeys)...), Sep))
}

func EvidenceHash(pairs []string) string {
	seen := map[string]bool{}
	var uniq []string
	for _, p := range pairs {
		if !seen[p] {
			seen[p] = true
			uniq = append(uniq, p)
		}
	}
	return Sha256Hex(strings.Join(sortedBytes(uniq), Sep))
}

func uuidHex() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return hex.EncodeToString(b)
}

func NewRunID() string      { return "run_" + uuidHex() }
func NewWorkflowID() string { return "wf_" + uuidHex() }

// NewUUID returns a canonical UUIDv4 string.
func NewUUID() string {
	h := uuidHex()
	return h[:8] + "-" + h[8:12] + "-" + h[12:16] + "-" + h[16:20] + "-" + h[20:]
}
